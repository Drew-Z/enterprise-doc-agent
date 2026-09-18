"""Task-scoped operations for the approved existing-domain routing change.

Run through runpy from the repository root. No deletion, retries or mail sending.
Private cloud snapshots remain in the existing centralized recovery group.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from infra.cloudflare_mail.deploy import (
    ACCOUNT_ID,
    EMPTY_DATABASE_QUERY,
    WORKER_NAME,
    Api,
    DeploymentError,
    PreparedPackage,
    active_deployment,
    disable_worker_logging,
    json_object,
    prepare,
    save_state,
    verify_worker,
    verify_worker_version,
    worker_metadata,
)

TASK = Path(__file__).resolve().parent
REPO = TASK.parents[2]
RECOVERY = Path(r"D:\Agent\codex\backups\tasks\20260918T015708.345Z-saas-mail-routing-reuse")
BASELINE = RECOVERY / "files/cloudflare/cloud-before.json"
BUNDLE = REPO / ".trellis/tasks/09-16-saas-cloudflare-mail-package/delivery/bundle"
DOMAIN = "playarchive.eu.cc"
ZONE = "18d121f8bd5d2692d448ebdbe7ea3071"
DATABASE = "8521706f-87c6-43af-8866-cc0ce95dd5d9"
ACCOUNT = f"/accounts/{ACCOUNT_ID}"
SCRIPT = f"{ACCOUNT}/workers/scripts/{WORKER_NAME}"
ZONE_PATH = f"/zones/{ZONE}"
ADDRESSES = [f"docagent{name}@{DOMAIN}" for name in ("owner01", "member01")]
OTHER_DOMAINS = {
    "avi.dpdns.org",
    "gamelab.eu.cc",
    "koe.cc.cd",
    "mcv.qzz.io",
    "mut.ccwu.cc",
    "playlab.eu.cc",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentError(message)


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def identity(verified: dict[str, Any]) -> dict[str, Any]:
    return {key: verified[key] for key in ("deployment_id", "version_id")}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def stable_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: item["id"])


def load_baseline() -> dict[str, Any]:
    manifest = json.loads((RECOVERY / "manifest.json").read_text(encoding="utf-8"))
    records = [x for x in manifest["remote_resource_snapshots"] if x["path"] == str(BASELINE)]
    require(len(records) == 1, "Private baseline reference is missing or ambiguous")
    raw = BASELINE.read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == records[0]["sha256"], "Private baseline hash changed"
    )
    return json_object(raw)


def database_query(api: Api) -> list[dict[str, Any]]:
    result = api.request(
        "POST",
        f"{ACCOUNT}/d1/database/{DATABASE}/query",
        body={"sql": EMPTY_DATABASE_QUERY + " SELECT id,name FROM address ORDER BY id;"},
    )["result"]
    if not isinstance(result, list) or len(result) != 3:
        raise DeploymentError("Unexpected D1 query result")
    require(
        all(isinstance(x, dict) and x.get("success") is True for x in result), "D1 query failed"
    )
    return result


def check_target(
    api: Api, baseline: dict[str, Any], *, expected_new: list[str]
) -> list[dict[str, Any]]:
    routing = api.object("GET", ZONE_PATH + "/email/routing")
    require(
        routing.get("enabled") is True and routing.get("status") == "ready", "Routing not ready"
    )
    dns = api.listing(ZONE_PATH + "/dns_records")
    require(stable_list(dns) == stable_list(baseline["dns"]), "Target DNS changed")
    catch = api.object("GET", ZONE_PATH + "/email/routing/rules/catch_all")
    require(catch == baseline["catch_all"], "Original catch-all changed")
    rules = api.listing(ZONE_PATH + "/email/routing/rules")
    old_ids = {r["id"] for r in baseline["rules"]}
    retained = [r for r in rules if r["id"] in old_ids]
    require(
        stable_list(retained) == stable_list(baseline["rules"]), "An original routing rule changed"
    )
    additions = [r for r in rules if r["id"] not in old_ids]
    require(len(additions) == len(expected_new), "Unexpected added routing rules")
    for address in expected_new:
        found = [r for r in additions if r.get("matchers") == rule_for(address)["matchers"]]
        require(len(found) == 1, "Expected exact routing rule missing or duplicated")
        require(rule_matches(found[0], address), "Exact routing rule action differs")
    return rules


def rule_for(address: str) -> dict[str, Any]:
    require(address in ADDRESSES, "Address outside approved scope")
    owner = "owner01" if address == ADDRESSES[0] else "member01"
    return {
        "name": f"DocAgent {owner} private mailbox",
        "enabled": True,
        "matchers": [{"type": "literal", "field": "to", "value": address}],
        "actions": [{"type": "worker", "value": [WORKER_NAME]}],
    }


def rule_matches(rule: dict[str, Any], address: str) -> bool:
    expected = rule_for(address)
    return all(rule.get(k) == expected[k] for k in ("enabled", "matchers", "actions"))


def overlay(package: PreparedPackage) -> PreparedPackage:
    config = copy.deepcopy(package.config)
    config["vars"]["DEFAULT_DOMAINS"] = [DOMAIN]
    config["vars"]["DOMAINS"] = [DOMAIN, *package.config["vars"]["DOMAINS"]]
    require(
        {k for k in config["vars"] if config["vars"][k] != package.config["vars"][k]}
        == {"DEFAULT_DOMAINS", "DOMAINS"},
        "Unexpected variable changes",
    )
    return PreparedPackage(config, package.files, package.manifest_sha256)


def metadata_for(package: PreparedPackage) -> dict[str, Any]:
    metadata = worker_metadata(overlay(package), DATABASE, "")
    metadata["assets"].pop("jwt")
    metadata["keep_assets"] = True
    metadata["keep_bindings"] = ["secret_text"]
    return metadata


def capture(api: Api, package: PreparedPackage) -> dict[str, Any]:
    require(not BASELINE.exists(), "Baseline already exists; inspect it instead of replacing")
    verified = verify_worker_version(api, package, DATABASE)
    snapshot: dict[str, Any] = {"captured_at": stamp(), "worker_verified": verified}
    for key, path in {
        "worker_version": SCRIPT + "/versions/" + verified["version_id"],
        "worker_settings": SCRIPT + "/script-settings",
        "worker_subdomain": SCRIPT + "/subdomain",
        "database": f"{ACCOUNT}/d1/database/{DATABASE}",
        "zone": ZONE_PATH,
        "routing": ZONE_PATH + "/email/routing",
        "catch_all": ZONE_PATH + "/email/routing/rules/catch_all",
    }.items():
        snapshot[key] = api.object("GET", path)
    snapshot["worker_schedules"] = api.request("GET", SCRIPT + "/schedules")["result"]
    snapshot["worker_domains"] = api.listing(ACCOUNT + "/workers/domains")
    snapshot["routing_dns"] = api.request("GET", ZONE_PATH + "/email/routing/dns")["result"]
    snapshot["dns"] = api.listing(ZONE_PATH + "/dns_records")
    snapshot["rules"] = api.listing(ZONE_PATH + "/email/routing/rules")
    snapshot["database_query"] = database_query(api)
    zones = [
        v
        for v in api.listing("/zones")
        if v["name"] in OTHER_DOMAINS and v.get("account", {}).get("id") == ACCOUNT_ID
    ]
    require(len(zones) == 6, "Other zone inventory differs")
    snapshot["other_zone_rules"] = {
        v["id"]: {
            "name": v["name"],
            "rules": api.listing(f"/zones/{v['id']}/email/routing/rules"),
            "catch_all": api.object("GET", f"/zones/{v['id']}/email/routing/rules/catch_all"),
        }
        for v in zones
    }
    require(active_deployment(api) == identity(verified), "Deployment drift during capture")
    require(snapshot["zone"]["name"] == DOMAIN, "Wrong zone name")
    require(snapshot["zone"]["account"]["id"] == ACCOUNT_ID, "Wrong zone account")
    require(
        snapshot["routing"].get("enabled") is True and snapshot["routing"].get("status") == "ready",
        "Routing not ready",
    )
    require(snapshot["catch_all"].get("enabled") is True, "Original catch-all is disabled")
    require(
        not any(
            m.get("value", "").lower() in ADDRESSES
            for r in snapshot["rules"]
            for m in r["matchers"]
        ),
        "Existing exact address conflict",
    )
    raw = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode()
    with BASELINE.open("xb") as file:
        file.write(raw)
    sha = hashlib.sha256(raw).hexdigest()
    require(hashlib.sha256(BASELINE.read_bytes()).hexdigest() == sha, "Snapshot hash mismatch")
    manifest = json.loads((RECOVERY / "manifest.json").read_text(encoding="utf-8"))
    manifest["remote_resource_snapshots"].append(
        {
            "path": str(BASELINE),
            "sha256": sha,
            "bytes": len(raw),
            "sensitive": True,
            "captured_at": snapshot["captured_at"],
            "verified": True,
            "scope": "Worker identity/config, D1, target DNS, seven zone Routing rules",
        }
    )
    save_state(RECOVERY / "manifest.json", manifest)
    return {
        "private_snapshot": str(BASELINE),
        "sha256": sha,
        "worker": verified,
        "routing_enabled": True,
        "routing_status": "ready",
        "dns_count": len(snapshot["dns"]),
        "original_rule_count": len(snapshot["rules"]),
        "other_zones_count": len(zones),
        "catch_all_id": snapshot["catch_all"]["id"],
        "database_counts": snapshot["database_query"][0]["results"],
        "addresses": [x["name"] for x in snapshot["database_query"][2]["results"]],
        "before_acceptance": {"new_domain_configured": False, "new_exact_rules_present": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("capture", "worker-plan", "worker-apply", "routing-apply", "verify")
    )
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    state_path = (TASK / args.state).resolve()
    require(
        state_path.parent == TASK and not state_path.exists(), "Use a new task-local state filename"
    )
    state: dict[str, Any] = {
        "action": args.action,
        "started_at": stamp(),
        "status": "started",
        "operations": [],
        "real_email_sent": False,
        "real_email_received_verified": False,
    }
    save_state(state_path, state)
    api = None
    try:
        package = prepare(BUNDLE)
        with httpx.Client() as client:
            api = Api(client, os.environ["CLOUDFLARE_API_TOKEN"])

            def mutation(name: str, operation: Any) -> Any:
                step = {"name": name, "status": "started", "started_at": stamp()}
                state["operations"].append(step)
                save_state(state_path, state)
                result = operation()
                step.update(status="succeeded", finished_at=stamp())
                save_state(state_path, state)
                return result

            if args.action == "capture":
                state["result"] = capture(api, package)
            else:
                baseline = load_baseline()
                if args.action in ("worker-plan", "worker-apply"):
                    verified = verify_worker_version(api, package, DATABASE)
                    require(
                        identity(verified) == identity(baseline["worker_verified"]),
                        "Worker deployment drift",
                    )
                    require(
                        verified["script_etag"] == baseline["worker_verified"]["script_etag"],
                        "Script identity drift",
                    )
                    check_target(api, baseline, expected_new=[])
                    state["metadata"] = metadata_for(package)
                    state["variable_changes"] = {
                        k: {
                            "before": package.config["vars"][k],
                            "after": overlay(package).config["vars"][k],
                        }
                        for k in ("DEFAULT_DOMAINS", "DOMAINS")
                    }
                    state["before"] = verified
                    save_state(state_path, state)
                    if args.action == "worker-apply":
                        require(
                            active_deployment(api) == identity(verified),
                            "Worker drift before upload",
                        )
                        mutation(
                            "update_worker_domain_variables",
                            lambda: api.request(
                                "PUT",
                                SCRIPT,
                                files={
                                    "metadata": (
                                        "metadata.json",
                                        json.dumps(state["metadata"]).encode(),
                                        "application/json",
                                    ),
                                    "worker.js": (
                                        "worker.js",
                                        package.files["worker.js"],
                                        "application/javascript+module",
                                    ),
                                },
                            ),
                        )
                        state["logging_disable"] = mutation(
                            "disable_worker_logging", lambda: disable_worker_logging(api)
                        )
                        state["result"] = verify_worker(
                            api, overlay(package), DATABASE, logging_disable_acknowledged=True
                        )
                        require(
                            state["result"]["script_etag"] == verified["script_etag"],
                            "Uploaded script bytes differ",
                        )
                else:
                    worker_receipt = json.loads(
                        (TASK / "worker-apply.json").read_text(encoding="utf-8")
                    )
                    require(worker_receipt["status"] == "passed", "Worker update not verified")
                    current = verify_worker(
                        api,
                        overlay(package),
                        DATABASE,
                        logging_disable_acknowledged=worker_receipt["logging_disable"][
                            "acknowledged"
                        ],
                    )
                    require(
                        identity(current) == identity(worker_receipt["result"]),
                        "Worker changed after update",
                    )
                    query = database_query(api)
                    database = api.object("GET", f"{ACCOUNT}/d1/database/{DATABASE}")
                    require(
                        all(
                            database.get(key) == baseline["database"].get(key)
                            for key in ("uuid", "name", "read_replication")
                        )
                        and database.get("read_replication") == {"mode": "disabled"},
                        "D1 identity or replication settings changed",
                    )
                    old_rows = baseline["database_query"][2]["results"]
                    require(
                        all(r in query[2]["results"] for r in old_rows),
                        "Original mailbox rows changed",
                    )
                    require(
                        query[1]["results"] == baseline["database_query"][1]["results"],
                        "D1 private settings changed",
                    )
                    require(
                        {r["name"] for r in query[2]["results"]}
                        == {r["name"] for r in old_rows} | set(ADDRESSES),
                        "Unexpected mailbox set",
                    )
                    if args.action == "routing-apply":
                        browser = json.loads(
                            (TASK / "public-new-domain-02/report.json").read_text(encoding="utf-8")
                        )
                        require(
                            browser["status"] == "passed"
                            and browser["domain"] == DOMAIN
                            and browser["address_prefix"] == "docagent",
                            "New mailbox browser gate failed",
                        )
                        done: list[str] = []
                        check_target(api, baseline, expected_new=done)
                        for address in ADDRESSES:
                            rules = api.listing(ZONE_PATH + "/email/routing/rules")
                            conflicts = [
                                r
                                for r in rules
                                if any(m.get("value", "").lower() == address for m in r["matchers"])
                            ]
                            require(
                                not conflicts,
                                "Exact rule appeared before creation; inspect before reuse",
                            )
                            rule = mutation(
                                "create_exact_rule_" + address,
                                lambda address=address: api.object(
                                    "POST",
                                    ZONE_PATH + "/email/routing/rules",
                                    body=rule_for(address),
                                ),
                            )
                            require(rule_matches(rule, address), "Created rule differs")
                            readback = api.object(
                                "GET", ZONE_PATH + "/email/routing/rules/" + rule["id"]
                            )
                            require(rule_matches(readback, address), "Rule readback differs")
                            state.setdefault("created_rules", []).append(readback)
                            done.append(address)
                            check_target(api, baseline, expected_new=done)
                            save_state(state_path, state)
                    rules = check_target(api, baseline, expected_new=ADDRESSES)
                    for zone, original in baseline["other_zone_rules"].items():
                        existing = api.listing(f"/zones/{zone}/email/routing/rules")
                        catch = api.object("GET", f"/zones/{zone}/email/routing/rules/catch_all")
                        require(
                            stable_list(existing) == stable_list(original["rules"])
                            and catch == original["catch_all"],
                            "Another zone routing changed",
                        )
                    schedules = api.request("GET", SCRIPT + "/schedules")["result"]
                    require(schedules == baseline["worker_schedules"], "Worker schedules changed")
                    domains = api.listing(ACCOUNT + "/workers/domains")
                    require(
                        stable_list(domains) == stable_list(baseline["worker_domains"]),
                        "Worker custom domains changed",
                    )
                    require(
                        active_deployment(api) == identity(current),
                        "Deployment drift during final checks",
                    )
                    state["result"] = {
                        "worker": current,
                        "database_counts": query[0]["results"],
                        "addresses": [r["name"] for r in query[2]["results"]],
                        "exact_rules": [
                            r for r in rules if any(rule_matches(r, addr) for addr in ADDRESSES)
                        ],
                        "original_dns_and_rules_unchanged": True,
                        "other_six_zones_routing_unchanged": True,
                        "d1_settings_unchanged": True,
                        "d1_identity_and_replication_unchanged": True,
                        "worker_domains_and_schedules_unchanged": True,
                        "original_catch_all_sha256": digest(baseline["catch_all"]),
                    }
            state["status"] = "passed"
    except Exception as error:
        state["status"] = "failed_needs_inspection"
        state["error_type"] = type(error).__name__
        state["safe_error"] = (
            str(error)
            if isinstance(error, DeploymentError)
            else "Unexpected failure; raw error withheld"
        )
    finally:
        state["finished_at"] = stamp()
        state["api_events"] = api.events if api else []
        save_state(state_path, state)
    print(
        json.dumps(
            {
                "status": state["status"],
                "state": str(state_path),
                "safe_error": state.get("safe_error"),
            }
        )
    )
    return 0 if state["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
