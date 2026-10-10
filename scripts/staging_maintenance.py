from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

try:
    from scripts.render_k8s_phase import select_phase
except ModuleNotFoundError:
    from render_k8s_phase import select_phase  # type: ignore[import-not-found,no-redef]

NAMESPACE = "enterprise-doc-agent-staging"
APPLICATIONS = {f"enterprise-doc-{name}" for name in ("api", "worker", "consumer", "web")}
REQUIRED_STEPS = {
    "prerequisites",
    "maintenance_prepare",
    "migration",
    "workloads",
    "maintenance_verify",
}
SKIPPED_STEPS = {
    "rollout",
    "embedding_rollout",
    "cluster_smoke",
    "authenticated_smoke",
    "governance_smoke",
    "browser_identity",
}


class MaintenanceError(ValueError):
    """A maintenance candidate or live inventory cannot establish a paused target."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaintenanceError("inventory must be a JSON object")
    return value


def _documents(path: Path) -> list[dict[str, Any]]:
    return [
        item
        for item in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _vector(config: dict[str, Any]) -> dict[str, str]:
    metadata = config.get("metadata", {})
    if (
        config.get("kind") != "ConfigMap"
        or metadata.get("name") != "enterprise-doc-config"
        or metadata.get("namespace") != NAMESPACE
    ):
        raise MaintenanceError("vector configuration must come from the expected ConfigMap")
    data = config.get("data", {})
    if not isinstance(data, dict):
        raise MaintenanceError("vector configuration is invalid")
    selected = {
        key: value
        for key, value in data.items()
        if key.startswith("EMBEDDING__") or key == "RETRIEVAL__REQUIRE_VECTOR_EVIDENCE"
    }
    required = {
        "EMBEDDING__" + suffix
        for suffix in ("PROVIDER", "BASE_URL", "MODEL_NAME", "DIMENSION", "VERSION")
    }
    if not required <= selected.keys() or any(
        not isinstance(v, str) or not v.strip() for v in selected.values()
    ):
        raise MaintenanceError("vector configuration is incomplete")
    return selected


def _images(deployment: dict[str, Any]) -> dict[str, str]:
    spec = deployment.get("spec", {}).get("template", {}).get("spec", {})
    containers = spec.get("containers", [])
    if len(containers) != 1 or spec.get("initContainers"):
        raise MaintenanceError("maintenance requires the reviewed single-container application")
    container = containers[0]
    name, image = container.get("name"), container.get("image")
    if (
        not isinstance(name, str)
        or not isinstance(image, str)
        or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image)
    ):
        raise MaintenanceError("application image must use an immutable digest")
    if any(entry.get("name", "").startswith("EMBEDDING__") for entry in container.get("env", [])):
        raise MaintenanceError("application must not override vector configuration")
    return {name: image}


def paused_workloads(
    source: Path, live_config: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    documents = _documents(source)
    configs = [
        d
        for d in documents
        if d.get("kind") == "ConfigMap"
        and d.get("metadata", {}).get("name") == "enterprise-doc-config"
    ]
    if len(configs) != 1:
        raise MaintenanceError("candidate needs exactly one vector configuration")
    expected, live = _vector(configs[0]), _vector(_json(live_config))
    if expected != live:
        raise MaintenanceError(
            "vector configuration changed; maintenance cannot run provider validation"
        )
    applications: dict[str, dict[str, Any]] = {}
    for item in select_phase(documents, "workloads"):
        name = item.get("metadata", {}).get("name")
        if name == "enterprise-doc-redis":
            continue
        if (
            name not in APPLICATIONS
            or name in applications
            or item.get("kind") != "Deployment"
            or item.get("metadata", {}).get("namespace") != NAMESPACE
        ):
            raise MaintenanceError("candidate applications are missing, duplicated or unsupported")
        _images(item)
        paused = copy.deepcopy(item)
        paused["spec"]["replicas"] = 0
        applications[name] = paused
    if applications.keys() != APPLICATIONS:
        raise MaintenanceError("candidate must contain exactly the four reviewed applications")
    return [applications[name] for name in sorted(applications)], expected


def _items(path: Path) -> list[dict[str, Any]]:
    items = _json(path).get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise MaintenanceError("live inventory must contain a complete items list")
    return items


def _paused(deployments: Path, pods: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in _items(deployments):
        name = item.get("metadata", {}).get("name")
        if name not in APPLICATIONS:
            if name != "enterprise-doc-redis":
                raise MaintenanceError("unreviewed workload in maintenance namespace")
            continue
        if (
            name in found
            or item.get("kind") != "Deployment"
            or item.get("metadata", {}).get("namespace") != NAMESPACE
        ):
            raise MaintenanceError("live application inventory is invalid")
        if item.get("spec", {}).get("replicas") != 0 or any(
            item.get("status", {}).get(key, 0) != 0
            for key in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
        ):
            raise MaintenanceError("all application workloads must already be paused")
        found[name] = item
    if found.keys() != APPLICATIONS:
        raise MaintenanceError("all four paused application workloads must be present")
    for pod in _items(pods):
        if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        labels = pod.get("metadata", {}).get("labels", {})
        if labels.get("app.kubernetes.io/name") == "enterprise-doc-redis":
            continue
        raise MaintenanceError("active Pod remains in maintenance namespace")
    return found


def _receipt(source: Path, workloads: Path, vector: dict[str, str], status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "maintenance-check",
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "namespace": NAMESPACE,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "workloads_sha256": hashlib.sha256(workloads.read_bytes()).hexdigest(),
        "vector_config_sha256": _hash(vector),
        "applications": sorted(APPLICATIONS),
        "target_replicas": 0,
    }


def prepare(
    source: Path, live_config: Path, deployments: Path, pods: Path, output: Path, report: Path
) -> dict[str, Any]:
    selected, vector = paused_workloads(source, live_config)
    _paused(deployments, pods)
    if source.resolve() == output.resolve():
        raise MaintenanceError("maintenance output must not overwrite the full candidate")
    output.write_text(
        yaml.safe_dump_all(selected, sort_keys=False, explicit_start=True), encoding="utf-8"
    )
    receipt = _receipt(source, output, vector, "maintenance_prepared")
    _write(report, receipt)
    return receipt


def verify(
    source: Path, workloads: Path, live_config: Path, deployments: Path, pods: Path, report: Path
) -> dict[str, Any]:
    expected, vector = paused_workloads(source, live_config)
    if _documents(workloads) != expected:
        raise MaintenanceError("maintenance workload manifest changed")
    live = _paused(deployments, pods)
    for item in expected:
        name = item["metadata"]["name"]
        if _images(live[name]) != _images(item):
            raise MaintenanceError(
                "installed application image does not match the paused candidate"
            )
    receipt = _receipt(source, workloads, vector, "maintenance_verified")
    _write(report, receipt)
    return receipt


def build_record(
    prepared: Path, verified: Path, outcomes: dict[str, str], commit: str, output: Path
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks_valid = False
    try:
        checks = {"prepared": _json(prepared), "verified": _json(verified)}
        before, after = checks["prepared"], checks["verified"]
        checks_valid = (
            before.get("status") == "maintenance_prepared"
            and after.get("status") == "maintenance_verified"
        )
        for key in ("source_sha256", "workloads_sha256", "vector_config_sha256"):
            checks_valid = (
                checks_valid
                and bool(re.fullmatch(r"[0-9a-f]{64}", str(before.get(key, ""))))
                and before[key] == after.get(key)
            )
    except (OSError, ValueError):
        checks = {}
    success = (
        checks_valid
        and bool(re.fullmatch(r"[0-9a-f]{40}", commit))
        and outcomes.keys() == REQUIRED_STEPS | SKIPPED_STEPS
        and all(outcomes[key] == "success" for key in REQUIRED_STEPS)
        and all(outcomes[key] == "skipped" for key in SKIPPED_STEPS)
    )
    record = {
        "schema_version": 1,
        "kind": "maintenance-deployment",
        "status": "blocked_external" if success else "failed",
        "maintenance_status": "installed_paused" if success else "unverified",
        "blocking_reason": (
            "Business workloads remain paused; resume and business acceptance "
            "require a separate reviewed execution."
        ),
        "executor_commit": commit,
        "generated_at": datetime.now(UTC).isoformat(),
        "outcomes": outcomes,
        "checks": checks,
        "supplier_validation": "not_executed",
        "production_capacity_approved": False,
    }
    _write(output, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a paused 4C4G maintenance deployment without supplier requests"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify"):
        command = commands.add_parser(name)
        for flag in (
            "input",
            "live-config",
            "live-deployments",
            "live-pods",
            "workloads",
            "report",
        ):
            command.add_argument("--" + flag, type=Path, required=True)
    record = commands.add_parser("record")
    for flag in ("prepared", "verified", "output"):
        record.add_argument("--" + flag, type=Path, required=True)
    record.add_argument("--outcomes-json", required=True)
    record.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        if args.command == "record":
            outcomes = json.loads(args.outcomes_json)
            if not isinstance(outcomes, dict) or any(
                not isinstance(v, str) for v in outcomes.values()
            ):
                raise MaintenanceError("workflow outcomes must be a string mapping")
            result = build_record(args.prepared, args.verified, outcomes, args.commit, args.output)
            if result["status"] == "failed":
                raise SystemExit(1)
        elif args.command == "prepare":
            prepare(
                args.input,
                args.live_config,
                args.live_deployments,
                args.live_pods,
                args.workloads,
                args.report,
            )
        else:
            verify(
                args.input,
                args.workloads,
                args.live_config,
                args.live_deployments,
                args.live_pods,
                args.report,
            )
    except (OSError, ValueError, yaml.YAMLError) as error:
        message = (
            str(error)
            if isinstance(error, MaintenanceError)
            else "maintenance input could not be validated"
        )
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
