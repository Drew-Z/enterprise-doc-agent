"""First deployment of the reviewed private mailbox, using Cloudflare REST APIs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

import httpx

ACCOUNT_ID = "2741446a7478f2d8a5ff31df7e077f17"
ZONE_ID = "5c093bf35698435cee3dff437c4e3545"
ZONE_NAME = "ciallobill.ccwu.cc"
WORKER_NAME = "docagent-private-mail"
HOSTNAME = "inbox.ciallobill.ccwu.cc"
PACKAGE_MANIFEST_SHA256 = "48ce026cc9360c5644b8b36c4c42fa6b5f72648e39daf96e7d8c176b5cb63f21"
API_ROOT = "https://api.cloudflare.com/client/v4"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
EMPTY_DATABASE_QUERY = (
    "SELECT (SELECT count(*) FROM raw_mails) AS mails,"
    "(SELECT count(*) FROM address) AS addresses,"
    "(SELECT count(*) FROM sendbox) AS sent;"
    " SELECT key,value FROM settings ORDER BY key;"
)

type Json = dict[str, Any]


class DeploymentError(RuntimeError):
    """Safe operator-facing error; never include remote bodies or credentials."""


def json_object(raw: bytes) -> Json:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise DeploymentError("Invalid JSON object") from None
    if not isinstance(value, dict):
        raise DeploymentError("Invalid JSON object")
    return value


@dataclass(frozen=True)
class PreparedPackage:
    """Validated bytes retained in memory to avoid a later upload/file race."""

    config: Json
    files: dict[str, bytes]
    manifest_sha256: str

    def describe(self) -> Json:
        return {
            "account_id": ACCOUNT_ID,
            "zone_id": ZONE_ID,
            "zone_name": ZONE_NAME,
            "worker_name": WORKER_NAME,
            "database_name": WORKER_NAME,
            "hostname": HOSTNAME,
            "manifest_sha256": self.manifest_sha256,
            "worker_sha256": hashlib.sha256(self.files["worker.js"]).hexdigest(),
            "asset_count": sum(name.startswith("assets/") for name in self.files),
            "primary_location_hint": "apac",
            "read_replication": {"mode": "disabled"},
            "fixed_processing_region_guaranteed": False,
            "receive_only": True,
            "email_routing_changed": False,
            "real_email_sent": False,
        }


def prepare(bundle: Path, *, manifest_sha256: str = PACKAGE_MANIFEST_SHA256) -> PreparedPackage:
    """Verify the immutable package. The alternate trust anchor is for fixtures."""
    if not bundle.is_dir() or bundle.is_symlink() or bundle.is_junction():
        raise DeploymentError("Bundle must be a real directory")
    entries = list(bundle.rglob("*"))
    if any(path.is_symlink() or path.is_junction() for path in entries):
        raise DeploymentError("Bundle links are not allowed")
    raw_manifest = (bundle / "manifest.json").read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != manifest_sha256:
        raise DeploymentError("Bundle manifest differs from the reviewed package")
    manifest = json_object(raw_manifest)
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list) or not manifest_files:
        raise DeploymentError("Bundle manifest has no files")
    files: dict[str, bytes] = {}
    for entry in manifest_files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise DeploymentError("Bundle file entry is invalid")
        name = entry["path"]
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or name != relative.as_posix()
            or name in files
            or name == "manifest.json"
        ):
            raise DeploymentError("Bundle file path is invalid")
        path = bundle / name
        if not path.is_file():
            raise DeploymentError("Bundle file is missing")
        data = path.read_bytes()
        if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get(
            "sha256"
        ):
            raise DeploymentError("Bundle file differs from its manifest")
        files[name] = data
    actual = {p.relative_to(bundle).as_posix() for p in entries if p.is_file()}
    if actual != set(files) | {"manifest.json"}:
        raise DeploymentError("Bundle file set differs from its manifest")
    required = {"worker.js", "schema.sql", "private-settings.sql", "wrangler.jsonc"}
    if not required.issubset(files) or "assets/index.html" not in files:
        raise DeploymentError("Bundle required input is missing")
    config = json_object(files["wrangler.jsonc"])
    if (
        config.get("account_id") != ACCOUNT_ID
        or config.get("name") != WORKER_NAME
        or config.get("vars", {}).get("FRONTEND_URL") != "https://" + HOSTNAME
        or config.get("workers_dev") is not False
        or config.get("preview_urls") is not False
        or config.get("routes") != []
        or config.get("assets", {}).get("run_worker_first") is not True
    ):
        raise DeploymentError("Bundle configuration is outside this rollout")
    return PreparedPackage(config, files, manifest_sha256)


class Api:
    """Bounded Cloudflare-only transport with secret-free request evidence."""

    def __init__(self, client: httpx.Client, token: str) -> None:
        self.client = client
        self.token = token
        self.events: list[Json] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Json | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        bearer: str | None = None,
    ) -> Json:
        if method not in {"GET", "POST", "PUT", "PATCH"} or not path.startswith("/"):
            raise DeploymentError("Unsupported Cloudflare operation")
        if ".." in path or "#" in path or "://" in path:
            raise DeploymentError("Invalid Cloudflare path")
        event: Json = {"method": method, "path": path, "http": None, "success": False}
        self.events.append(event)
        try:
            with self.client.stream(
                method,
                API_ROOT + path,
                headers={
                    "Authorization": "Bearer " + (bearer or self.token),
                    "Connection": "close",
                },
                json=body,
                files=files,
                follow_redirects=False,
                timeout=120 if files else 40,
            ) as response:
                event["http"] = response.status_code
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise DeploymentError("Cloudflare response exceeded the size limit")
                payload = json_object(bytes(raw))
                errors = payload.get("errors") or []
                codes = [
                    item["code"]
                    for item in errors
                    if isinstance(item, dict) and isinstance(item.get("code"), int)
                ]
                event["error_codes"] = codes
                if response.status_code >= 300 or payload.get("success") is not True:
                    raise DeploymentError(
                        f"Cloudflare request failed (HTTP {response.status_code}, codes {codes})"
                    )
                event["success"] = True
                return payload
        except httpx.HTTPError as error:
            event["transport_error"] = True
            event["transport_error_type"] = type(error).__name__
            event["mutation_outcome_unknown"] = method != "GET"
            raise DeploymentError(
                "Cloudflare transport failed; inspect state before retry"
            ) from None

    def object(self, method: str, path: str, *, body: Json | None = None) -> Json:
        result = self.request(method, path, body=body).get("result")
        if not isinstance(result, dict):
            raise DeploymentError("Cloudflare result is not an object")
        return result

    def listing(self, path: str) -> list[Json]:
        found: list[Json] = []
        for page in range(1, 101):
            payload = self.request("GET", f"{path}?per_page=100&page={page}")
            items = payload.get("result")
            if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
                raise DeploymentError("Cloudflare list result is invalid")
            found.extend(items)
            info = payload.get("result_info") or {}
            total = info.get("total_count")
            if isinstance(total, int):
                if len(found) == total:
                    return found
                if not items or len(found) > total:
                    raise DeploymentError("Cloudflare list is incomplete or changed")
            elif info.get("total_pages") == page or (not info and len(items) < 100):
                return found
            elif not items:
                raise DeploymentError("Cloudflare pagination is incomplete")
        raise DeploymentError("Cloudflare pagination exceeded the page limit")


def preflight(
    api: Api, *, owned_database_id: str | None = None, owned_worker: bool = False
) -> Json:
    """Reject conflicting names/hosts and wrong accounts before any cloud write."""
    zone = api.object("GET", f"/zones/{ZONE_ID}")
    if (
        zone.get("account", {}).get("id") != ACCOUNT_ID
        or zone.get("name") != ZONE_NAME
        or zone.get("status") != "active"
        or zone.get("type") != "full"
        or zone.get("paused") is not False
    ):
        raise DeploymentError("Zone or account does not match this rollout")
    databases = api.listing(f"/accounts/{ACCOUNT_ID}/d1/database")
    owned = [item for item in databases if item.get("name") == WORKER_NAME]
    if owned_database_id is None:
        if owned:
            raise DeploymentError("Existing database conflicts with this rollout")
    elif len(owned) != 1 or owned[0].get("uuid") != owned_database_id:
        raise DeploymentError("Recorded database does not match the current dedicated resource")
    workers = api.listing(f"/accounts/{ACCOUNT_ID}/workers/scripts")
    matching_workers = [item for item in workers if item.get("id") == WORKER_NAME]
    if owned_worker:
        if owned_database_id is None or len(matching_workers) != 1:
            raise DeploymentError("Recorded Worker does not match the current dedicated resource")
    elif matching_workers:
        raise DeploymentError("Existing Worker conflicts with this rollout")
    domains = api.listing(f"/accounts/{ACCOUNT_ID}/workers/domains")
    if any(item.get("hostname") == HOSTNAME for item in domains):
        raise DeploymentError("Existing custom domain conflicts with this rollout")
    dns = api.listing(f"/zones/{ZONE_ID}/dns_records")
    if any(item.get("name") == HOSTNAME for item in dns):
        raise DeploymentError("Existing DNS record conflicts with this rollout")
    return {
        "account_id": ACCOUNT_ID,
        "zone_id": ZONE_ID,
        "zone_status": zone["status"],
        "existing_database_count": len(databases),
        "existing_worker_count": len(workers),
        "existing_custom_domain_count": len(domains),
        "zone_dns_record_count": len(dns),
        "target_names_available": owned_database_id is None and not owned_worker,
        "web_hostname_available": True,
        "recorded_worker_matched": owned_worker,
    }


def validate_secrets(values: Json, api_token: str) -> dict[str, str]:
    names = {"PASSWORDS", "ADMIN_PASSWORDS", "JWT_SECRET"}
    if set(values) != names or not all(isinstance(value, str) for value in values.values()):
        raise DeploymentError("Exactly three string secret bindings are required")
    keys: list[str] = []
    for name in ("PASSWORDS", "ADMIN_PASSWORDS"):
        try:
            group = json.loads(values[name])
        except ValueError:
            raise DeploymentError("Secret arrays are invalid") from None
        if not isinstance(group, list) or not group or not all(isinstance(x, str) for x in group):
            raise DeploymentError("Secret arrays are invalid")
        keys.extend(group)
    keys.append(values["JWT_SECRET"])
    if (
        any(len(key) < 32 or any(not 33 <= ord(char) <= 126 for char in key) for key in keys)
        or len(keys) != len(set(keys))
        or api_token in keys
    ):
        raise DeploymentError("Secret values must be strong, distinct ASCII keys")
    return {name: values[name] for name in sorted(names)}


def save_state(path: Path, state: Json) -> None:
    """Atomic replace within the task; never create a backup beside source files."""
    raw = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as file:
        temporary = Path(file.name)
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def asset_payload(package: PreparedPackage) -> tuple[Json, dict[str, tuple[str, bytes, str]]]:
    mime_types = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript",
        ".css": "text/css",
        ".wasm": "application/wasm",
        ".ico": "image/x-icon",
        ".png": "image/png",
        ".webmanifest": "application/manifest+json",
    }
    manifest: Json = {}
    uploads: dict[str, tuple[str, bytes, str]] = {}
    for path, data in sorted(package.files.items()):
        if not path.startswith("assets/"):
            continue
        mime = mime_types.get(PurePosixPath(path).suffix, "application/octet-stream")
        digest = hashlib.sha256(mime.encode() + b"\0" + data).hexdigest()[:32]
        manifest["/" + path.removeprefix("assets/")] = {"hash": digest, "size": len(data)}
        uploads[digest] = (digest, base64.b64encode(data), mime)
    return manifest, uploads


def worker_metadata(package: PreparedPackage, database_id: str, assets_token: str) -> Json:
    bindings: list[Json] = [
        {"type": "d1", "name": "DB", "database_id": database_id},
        {"type": "assets", "name": "ASSETS"},
    ]
    for name, value in package.config["vars"].items():
        if isinstance(value, str):
            bindings.append({"name": name, "type": "plain_text", "text": value})
        else:
            bindings.append({"name": name, "type": "json", "json": value})
    return {
        "main_module": "worker.js",
        "compatibility_date": package.config["compatibility_date"],
        "compatibility_flags": package.config["compatibility_flags"],
        "bindings": bindings,
        "assets": {"jwt": assets_token, "config": {"run_worker_first": True}},
        "observability": {"enabled": False},
        "logpush": False,
        "tail_consumers": [],
    }


def active_deployment(api: Api) -> Json:
    """The documented deployments list puts the deployment serving traffic first."""
    script = f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"
    deployments = api.object("GET", script + "/deployments").get("deployments")
    if not isinstance(deployments, list) or not deployments or not isinstance(deployments[0], dict):
        raise DeploymentError("Worker active deployment could not be verified")
    current = deployments[0]
    versions = current.get("versions")
    if (
        not isinstance(versions, list)
        or len(versions) != 1
        or not isinstance(versions[0], dict)
        or versions[0].get("percentage") != 100
    ):
        raise DeploymentError("Worker must serve exactly one version at 100 percent")
    try:
        return {
            "deployment_id": str(UUID(current["id"])),
            "version_id": str(UUID(versions[0]["version_id"])),
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DeploymentError("Worker deployment identifiers are invalid") from None


def verify_worker_version(api: Api, package: PreparedPackage, database_id: str) -> Json:
    script = f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"
    current = active_deployment(api)
    version = api.object("GET", script + "/versions/" + current["version_id"])
    resources = version.get("resources")
    if version.get("id") != current["version_id"] or not isinstance(resources, dict):
        raise DeploymentError("Worker version resources could not be verified")
    runtime = resources.get("script_runtime")
    if not isinstance(runtime, dict):
        raise DeploymentError("Worker runtime configuration could not be verified")
    assets = runtime.get("assets")
    if (
        not isinstance(assets, dict)
        or assets.get("raw_run_worker_first") is not True
        or assets.get("serve_directly") is not False
    ):
        raise DeploymentError("Worker assets are not configured to run the private entry first")
    if (
        runtime.get("compatibility_date") != package.config["compatibility_date"]
        or runtime.get("compatibility_flags") != package.config["compatibility_flags"]
    ):
        raise DeploymentError("Worker runtime differs from the reviewed configuration")
    deployed_script = resources.get("script")
    if (
        not isinstance(deployed_script, dict)
        or sorted(deployed_script.get("handlers") or []) != ["email", "fetch"]
        or not isinstance(deployed_script.get("etag"), str)
        or not deployed_script["etag"]
    ):
        raise DeploymentError("Worker handlers or script identity could not be verified")
    bindings = resources.get("bindings")
    if not isinstance(bindings, list) or not all(isinstance(x, dict) for x in bindings):
        raise DeploymentError("Worker bindings could not be verified")
    by_name = {item.get("name"): item for item in bindings}
    expected = set(package.config["vars"]) | {
        "DB",
        "ASSETS",
        "PASSWORDS",
        "ADMIN_PASSWORDS",
        "JWT_SECRET",
    }
    if set(by_name) != expected or len(bindings) != len(expected):
        raise DeploymentError("Worker bindings differ from the receive-only configuration")
    db = by_name["DB"]
    if db.get("type") != "d1" or db.get("database_id", db.get("id")) != database_id:
        raise DeploymentError("Worker database binding differs from the dedicated database")
    if by_name["ASSETS"].get("type") != "assets":
        raise DeploymentError("Worker assets binding is invalid")
    for name in ("PASSWORDS", "ADMIN_PASSWORDS", "JWT_SECRET"):
        if by_name[name].get("type") != "secret_text":
            raise DeploymentError("Worker private secret binding is missing")
    for name, value in package.config["vars"].items():
        actual = by_name[name]
        if isinstance(value, str):
            matches = actual.get("type") == "plain_text" and actual.get("text") == value
        else:
            matches = (
                actual.get("type") == "json"
                and type(actual.get("json")) is type(value)
                and actual["json"] == value
            )
        if not matches:
            raise DeploymentError("Worker variable differs from the reviewed configuration")
    return {
        **current,
        "script_etag": deployed_script["etag"],
        "compatibility_date": runtime["compatibility_date"],
        "compatibility_flags": runtime["compatibility_flags"],
        "handlers": sorted(deployed_script["handlers"]),
        "binding_names": sorted(expected),
        "dedicated_database_binding_verified": True,
        "private_secret_bindings_verified": True,
        "receive_only_bindings_verified": True,
        "assets_run_worker_first": True,
    }


def verify_worker(
    api: Api,
    package: PreparedPackage,
    database_id: str,
    *,
    logging_disable_acknowledged: bool = False,
) -> Json:
    script = f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"
    subdomain = api.object("GET", script + "/subdomain")
    if subdomain.get("enabled") is not False or subdomain.get("previews_enabled") is not False:
        raise DeploymentError("Worker alternate public entrances are not disabled")
    verified = verify_worker_version(api, package, database_id)
    settings = api.object("GET", script + "/script-settings")
    observability = settings.get("observability")
    # A null response alone does not prove the logging default. Accept it only
    # after this operation has acknowledged an explicit script-settings disable.
    disabled = (
        observability is None and "observability" in settings and logging_disable_acknowledged
    ) or (isinstance(observability, dict) and observability.get("enabled") is False)
    if (
        settings.get("logpush") is not False
        or "tail_consumers" not in settings
        or settings["tail_consumers"] not in (None, [])
        or not disabled
    ):
        raise DeploymentError("Worker persistent logging is not disabled")
    if isinstance(observability, dict):
        for kind in ("logs", "traces"):
            child = observability.get(kind)
            if child is not None and (
                not isinstance(child, dict)
                or child.get("enabled") is True
                or child.get("destinations") not in (None, [])
            ):
                raise DeploymentError("Worker logging or trace export is enabled")
    if active_deployment(api) != {key: verified[key] for key in ("deployment_id", "version_id")}:
        raise DeploymentError("Worker active deployment changed during verification")
    return {
        **verified,
        "workers_dev": False,
        "preview_urls": False,
        "persistent_worker_logs": False,
        "observability_readback": observability,
        "logging_disable_acknowledged": logging_disable_acknowledged,
        "logpush": settings["logpush"],
        "tail_consumers": settings["tail_consumers"],
    }


def disable_worker_logging(api: Api) -> Json:
    """Disable capture, persistence and export through the script-level API."""
    settings = {
        "logpush": False,
        "tail_consumers": [],
        "observability": {
            "enabled": False,
            "head_sampling_rate": 0,
            "logs": {
                "enabled": False,
                "invocation_logs": False,
                "persist": False,
                "head_sampling_rate": 0,
                "destinations": [],
            },
            "traces": {
                "enabled": False,
                "persist": False,
                "head_sampling_rate": 0,
                "destinations": [],
            },
        },
    }
    api.object(
        "PATCH",
        f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}/script-settings",
        body=settings,
    )
    return {"requested": settings, "acknowledged": True}


def asset_resume_source(path: Path, package: PreparedPackage) -> tuple[str, Json]:
    raw = path.read_bytes()
    prior = json_object(raw)
    completed = {s.get("name") for s in prior.get("steps", []) if s.get("status") == "succeeded"}
    steps = prior.get("steps") or [{}]
    last = steps[-1].get("name", "")
    if (
        prior.get("status") != "failed_needs_inspection"
        or prior.get("plan") != package.describe()
        or prior.get("worker_name")
        or "upload_closed_worker" in completed
        or not (last == "register_assets" or last.startswith("upload_asset_bucket_"))
        or not (
            {"initialize_schema.sql", "initialize_private-settings.sql"} <= completed
            or prior.get("database_initialized") is True
        )
    ):
        raise DeploymentError("Only an inspected failure during assets upload can be resumed")
    try:
        identifier = str(UUID(prior["database_id"]))
    except (ValueError, KeyError, TypeError):
        raise DeploymentError("Recorded database identifier is invalid") from None
    return identifier, {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


def verify_empty_database(package: PreparedPackage, checks: Any) -> Json:
    if (
        not isinstance(checks, list)
        or len(checks) != 2
        or any(not isinstance(item, dict) or item.get("success") is not True for item in checks)
        or checks[0].get("results") != [{"mails": 0, "addresses": 0, "sent": 0}]
    ):
        raise DeploymentError("Recorded database contains data or could not be verified")
    with sqlite3.connect(":memory:") as expected:
        expected.executescript(package.files["schema.sql"].decode())
        expected.executescript(package.files["private-settings.sql"].decode())
        expected_settings = dict(expected.execute("SELECT key,value FROM settings"))
    rows = checks[1].get("results")
    if (
        not isinstance(rows, list)
        or len(rows) != len(expected_settings)
        or any(not isinstance(row, dict) or not isinstance(row.get("key"), str) for row in rows)
        or {row["key"]: row.get("value") for row in rows} != expected_settings
    ):
        raise DeploymentError("Recorded database settings changed; resume stopped")
    return {
        "counts": checks[0]["results"][0],
        "settings_match_fixed_package": True,
        "query_metadata": [
            {
                key: item.get("meta", {}).get(key)
                for key in (
                    "served_by_region",
                    "served_by_colo",
                    "served_by_primary",
                    "changed_db",
                    "rows_written",
                )
            }
            for item in checks
        ],
    }


def publish_existing(
    package: PreparedPackage,
    api: Api,
    state_path: Path,
    *,
    source_path: Path,
    expected_version_id: str,
    expected_deployment_id: str,
    expected_script_etag: str,
) -> Json:
    """Publish a specifically inspected Worker; never replay its upload or secrets."""
    if state_path.exists():
        raise DeploymentError("State already exists; inspect it before any further action")
    raw = source_path.read_bytes()
    prior = json_object(raw)
    steps = prior.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
        raise DeploymentError("Publication source has no valid deployment steps")
    completed = {s.get("name") for s in steps if s.get("status") == "succeeded"}
    required = {
        "upload_closed_worker",
        "disable_workers_dev_and_previews",
        "install_secret_ADMIN_PASSWORDS",
        "install_secret_JWT_SECRET",
        "install_secret_PASSWORDS",
    }
    if (
        prior.get("status") != "failed_needs_inspection"
        or prior.get("plan") != package.describe()
        or prior.get("worker_name") != WORKER_NAME
        or prior.get("database_initialized") is not True
        or not required <= completed
        or steps[-1].get("name") != "verify_worker_configuration"
        or "attach_web_custom_domain" in {s.get("name") for s in steps}
        or prior.get("custom_domain")
    ):
        raise DeploymentError("Publication requires the inspected failure before domain attachment")
    try:
        database_id = str(UUID(prior["database_id"]))
        expected_identity = {
            "version_id": str(UUID(expected_version_id)),
            "deployment_id": str(UUID(expected_deployment_id)),
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DeploymentError("Publication resource identifiers are invalid") from None
    if not expected_script_etag or not expected_script_etag.isascii():
        raise DeploymentError("Publication requires an inspected script identity")
    state: Json = {
        "status": "running",
        "operation": "publish_existing_worker",
        "started_at": datetime.now(UTC).isoformat(),
        "plan": package.describe(),
        "database_id": database_id,
        "worker_name": WORKER_NAME,
        "source": {"path": str(source_path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()},
        "expected_worker": {**expected_identity, "script_etag": expected_script_etag},
        "steps": [],
        "api_events": api.events,
        "real_email_sent": False,
        "email_routing_changed": False,
        "resource_creation_replayed": False,
        "secret_upload_replayed": False,
    }
    with state_path.open("x", encoding="utf-8"):
        pass

    def step[T](name: str, action: Callable[[], T]) -> T:
        record = {"name": name, "status": "started"}
        state["steps"].append(record)
        save_state(state_path, state)
        result = action()
        record["status"] = "succeeded"
        save_state(state_path, state)
        return result

    account = f"/accounts/{ACCOUNT_ID}"
    script = account + f"/workers/scripts/{WORKER_NAME}"
    try:
        state["preflight"] = step(
            "verify_owned_resources_and_free_hostname",
            lambda: preflight(api, owned_database_id=database_id, owned_worker=True),
        )
        database = step(
            "verify_recorded_database",
            lambda: api.object("GET", account + f"/d1/database/{database_id}"),
        )
        if (
            database.get("uuid") != database_id
            or database.get("name") != WORKER_NAME
            or database.get("read_replication", {}).get("mode") != "disabled"
        ):
            raise DeploymentError("Recorded database differs from the dedicated resource")
        state["database_verification"] = step(
            "verify_initialized_database",
            lambda: verify_empty_database(
                package,
                api.request(
                    "POST",
                    account + f"/d1/database/{database_id}/query",
                    body={"sql": EMPTY_DATABASE_QUERY},
                ).get("result"),
            ),
        )
        before = step(
            "verify_inspected_worker_version",
            lambda: verify_worker_version(api, package, database_id),
        )
        if any(before[key] != value for key, value in state["expected_worker"].items()):
            raise DeploymentError("Current Worker differs from the inspected deployment")
        alternate = api.object("GET", script + "/subdomain")
        if alternate.get("enabled") is not False or alternate.get("previews_enabled") is not False:
            raise DeploymentError("Worker alternate public entrances are not disabled")
        state["logging_disable"] = step(
            "disable_persistent_logging", lambda: disable_worker_logging(api)
        )
        state["worker_verification"] = step(
            "verify_worker_configuration",
            lambda: verify_worker(api, package, database_id, logging_disable_acknowledged=True),
        )
        if any(
            state["worker_verification"][key] != value
            for key, value in state["expected_worker"].items()
        ):
            raise DeploymentError("Current Worker differs from the inspected deployment")
        domains = api.listing(account + "/workers/domains")
        dns = api.listing(f"/zones/{ZONE_ID}/dns_records")
        if any(item.get("hostname") == HOSTNAME for item in domains) or any(
            item.get("name") == HOSTNAME for item in dns
        ):
            raise DeploymentError("Existing web hostname appeared during preparation")
        if active_deployment(api) != expected_identity:
            raise DeploymentError("Worker active deployment changed before publication")
        domain = step(
            "attach_web_custom_domain",
            lambda: api.object(
                "PUT",
                account + "/workers/domains",
                body={
                    "hostname": HOSTNAME,
                    "service": WORKER_NAME,
                    "zone_id": ZONE_ID,
                },
            ),
        )
        state["custom_domain"] = {
            key: domain.get(key) for key in ("id", "hostname", "service", "zone_id", "cert_id")
        }
        if domain.get("hostname") != HOSTNAME or domain.get("service") != WORKER_NAME:
            raise DeploymentError("Attached custom domain does not match the planned Worker")
        state["status"] = "deployed_pending_https_validation"
        state["finished_at"] = datetime.now(UTC).isoformat()
        save_state(state_path, state)
        return state
    except Exception as error:
        state["status"] = "failed_needs_inspection"
        state["error"] = (
            str(error)
            if isinstance(error, DeploymentError)
            else "Unexpected local failure; inspect state"
        )
        save_state(state_path, state)
        if isinstance(error, DeploymentError):
            raise
        raise DeploymentError("Publication failed; inspect state before any retry") from None


def deploy(
    package: PreparedPackage,
    api: Api,
    secrets: Json,
    state_path: Path,
    *,
    resume_assets_from: Path | None = None,
) -> Json:
    """Create only new resources; a failed attempt must be inspected, not replayed."""
    if state_path.exists():
        raise DeploymentError("State already exists; inspect it before any further action")
    private = validate_secrets(secrets, api.token)
    owned_id: str | None = None
    source: Json | None = None
    if resume_assets_from is not None:
        owned_id, source = asset_resume_source(resume_assets_from, package)
    before = preflight(api, owned_database_id=owned_id)
    state: Json = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "plan": package.describe(),
        "preflight": before,
        "steps": [],
        "api_events": api.events,
        "real_email_sent": False,
        "email_routing_changed": False,
        "resume_source": source,
    }
    # Claim the evidence path without overwriting another run's report.
    with state_path.open("x", encoding="utf-8"):
        pass

    def step[T](name: str, action: Callable[[], T]) -> T:
        record = {"name": name, "status": "started"}
        state["steps"].append(record)
        save_state(state_path, state)
        result = action()
        record["status"] = "succeeded"
        save_state(state_path, state)
        return result

    account = f"/accounts/{ACCOUNT_ID}"
    script = account + f"/workers/scripts/{WORKER_NAME}"
    try:
        if owned_id is None:
            database = step(
                "create_dedicated_database",
                lambda: api.object(
                    "POST",
                    account + "/d1/database",
                    body={
                        "name": WORKER_NAME,
                        "primary_location_hint": "apac",
                        "read_replication": {"mode": "disabled"},
                    },
                ),
            )
        else:
            database = step(
                "verify_recorded_database",
                lambda: api.object("GET", account + f"/d1/database/{owned_id}"),
            )
        database_id = str(UUID(database["uuid"]))
        state["database_id"] = database_id
        state["database"] = {
            key: database.get(key) for key in ("name", "uuid", "jurisdiction", "read_replication")
        }
        save_state(state_path, state)
        if database.get("name") != WORKER_NAME:
            raise DeploymentError("New database has an unexpected name")
        if database.get("read_replication", {}).get("mode") != "disabled":
            raise DeploymentError("New database read replication is not disabled")
        query_path = account + f"/d1/database/{database_id}/query"

        def query(sql: str) -> list[Json]:
            result = api.request("POST", query_path, body={"sql": sql}).get("result")
            if (
                not isinstance(result, list)
                or not result
                or any(
                    not isinstance(item, dict) or item.get("success") is not True for item in result
                )
            ):
                raise DeploymentError("D1 query did not complete successfully")
            regions = state.setdefault("observed_query_regions", [])
            for item in result:
                region = item.get("meta", {}).get("served_by_region")
                if isinstance(region, str) and region not in regions:
                    regions.append(region)
            return result

        if owned_id is None:
            empty = step(
                "verify_new_database_empty",
                lambda: query(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*';"
                ),
            )
            if any(item.get("results") != [] for item in empty):
                raise DeploymentError("New database is not empty; initialization stopped")
            for filename in ("schema.sql", "private-settings.sql"):
                step(
                    "initialize_" + filename,
                    partial(query, package.files[filename].decode("utf-8")),
                )
        else:
            checks = step(
                "verify_initialized_database",
                lambda: query(EMPTY_DATABASE_QUERY),
            )
            state["database_verification"] = verify_empty_database(package, checks)
        state["database_initialized"] = True
        manifest, uploads = asset_payload(package)
        session = step(
            "register_assets",
            lambda: api.object(
                "POST", script + "/assets-upload-session", body={"manifest": manifest}
            ),
        )
        token = session.get("jwt")
        buckets = session.get("buckets")
        if not isinstance(token, str) or not token or not isinstance(buckets, list):
            raise DeploymentError("Asset session response is invalid")
        completion: str | None = token if not buckets else None
        state["asset_upload_files"] = []
        for index, bucket in enumerate(buckets):
            if (
                not isinstance(bucket, list)
                or not bucket
                or any(key not in uploads for key in bucket)
            ):
                raise DeploymentError("Asset session requested unknown files")
            # Small content-addressed requests survive slow uplinks better than a whole bucket.
            for number, key in enumerate(bucket):
                state["asset_upload_files"].append(
                    {"hash": key, "base64_bytes": len(uploads[key][1])}
                )
                response = step(
                    f"upload_asset_bucket_{index + 1}_file_{number + 1}",
                    partial(
                        api.request,
                        "POST",
                        account + "/workers/assets/upload?base64=true",
                        files={key: uploads[key]},
                        bearer=token,
                    ),
                )
                result = response.get("result", {})
                if isinstance(result, dict) and isinstance(result.get("jwt"), str):
                    completion = result["jwt"]
        if not completion:
            raise DeploymentError("Asset completion token was not returned")
        # Refresh the name check immediately before the overwrite-capable upload API.
        if any(item.get("id") == WORKER_NAME for item in api.listing(account + "/workers/scripts")):
            raise DeploymentError("Existing Worker appeared during preparation")
        metadata = worker_metadata(package, database_id, completion)
        step(
            "upload_closed_worker",
            lambda: api.request(
                "PUT",
                script,
                files={
                    "metadata": (
                        "metadata.json",
                        json.dumps(metadata).encode(),
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
        state["worker_name"] = WORKER_NAME
        step(
            "disable_workers_dev_and_previews",
            lambda: api.object(
                "POST", script + "/subdomain", body={"enabled": False, "previews_enabled": False}
            ),
        )
        alternate = api.object("GET", script + "/subdomain")
        if alternate.get("enabled") is not False or alternate.get("previews_enabled") is not False:
            raise DeploymentError("Worker alternate public entrances remain enabled")
        for name, value in private.items():
            step(
                "install_secret_" + name,
                partial(
                    api.object,
                    "PUT",
                    script + "/secrets",
                    body={"name": name, "text": value, "type": "secret_text"},
                ),
            )
        state["logging_disable"] = step(
            "disable_persistent_logging", lambda: disable_worker_logging(api)
        )
        state["worker_verification"] = step(
            "verify_worker_configuration",
            lambda: verify_worker(api, package, database_id, logging_disable_acknowledged=True),
        )
        domains = api.listing(account + "/workers/domains")
        dns = api.listing(f"/zones/{ZONE_ID}/dns_records")
        if any(item.get("hostname") == HOSTNAME for item in domains) or any(
            item.get("name") == HOSTNAME for item in dns
        ):
            raise DeploymentError("Existing web hostname appeared during preparation")
        domain = step(
            "attach_web_custom_domain",
            lambda: api.object(
                "PUT",
                account + "/workers/domains",
                body={"hostname": HOSTNAME, "service": WORKER_NAME, "zone_id": ZONE_ID},
            ),
        )
        state["custom_domain"] = {
            key: domain.get(key) for key in ("id", "hostname", "service", "zone_id", "cert_id")
        }
        if domain.get("hostname") != HOSTNAME or domain.get("service") != WORKER_NAME:
            raise DeploymentError("Attached custom domain does not match the planned Worker")
        state["status"] = "deployed_pending_https_validation"
        state["finished_at"] = datetime.now(UTC).isoformat()
        save_state(state_path, state)
        return state
    except Exception as error:
        state["status"] = "failed_needs_inspection"
        state["error"] = (
            str(error)
            if isinstance(error, DeploymentError)
            else "Unexpected local failure; inspect state"
        )
        save_state(state_path, state)
        if isinstance(error, DeploymentError):
            raise
        raise DeploymentError("Deployment failed; inspect state before any retry") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--publish-existing-from", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--secrets-file", type=Path)
    parser.add_argument("--resume-assets-from", type=Path)
    parser.add_argument("--expected-version-id")
    parser.add_argument("--expected-deployment-id")
    parser.add_argument("--expected-script-etag")
    args = parser.parse_args()
    try:
        package = prepare(args.bundle)
        if args.resume_assets_from is not None and not args.apply:
            raise DeploymentError("Asset resume requires --apply")
        if not args.preflight and not args.apply and args.publish_existing_from is None:
            print(json.dumps(package.describe(), ensure_ascii=False, indent=2))
            return 0
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise DeploymentError("CLOUDFLARE_API_TOKEN is not configured locally")
        with httpx.Client() as client:
            api = Api(client, token)
            if args.publish_existing_from is not None:
                if not all(
                    (
                        args.state,
                        args.expected_version_id,
                        args.expected_deployment_id,
                        args.expected_script_etag,
                    )
                ):
                    raise DeploymentError(
                        "Publication requires state and all inspected Worker identifiers"
                    )
                if args.state.resolve().is_relative_to(args.bundle.resolve()):
                    raise DeploymentError("Publication state must be outside the immutable bundle")
                result = publish_existing(
                    package,
                    api,
                    args.state,
                    source_path=args.publish_existing_from,
                    expected_version_id=args.expected_version_id,
                    expected_deployment_id=args.expected_deployment_id,
                    expected_script_etag=args.expected_script_etag,
                )
                print(
                    json.dumps(
                        {"status": result["status"], "state_path": str(args.state)},
                        ensure_ascii=False,
                    )
                )
            elif args.apply:
                if args.state is None or args.secrets_file is None:
                    raise DeploymentError("Apply requires --state and --secrets-file")
                if args.state.resolve().is_relative_to(args.bundle.resolve()):
                    raise DeploymentError("Deployment state must be outside the immutable bundle")
                if os.name == "nt":
                    checked = subprocess.run(
                        [
                            r"C:\Users\zhang\AppData\Local\Microsoft\WindowsApps\pwsh.exe",
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(Path(__file__).with_name("new-credentials.ps1")),
                            "-CheckOnly",
                            "-Path",
                            str(args.secrets_file.resolve()),
                        ],
                        check=False,
                        capture_output=True,
                        timeout=20,
                    )
                    if checked.returncode != 0:
                        raise DeploymentError("Credential file Windows DACL verification failed")
                elif args.secrets_file.stat().st_mode & 0o077:
                    raise DeploymentError("Credential file permissions allow other users")
                values = json_object(args.secrets_file.read_bytes())
                result = deploy(
                    package, api, values, args.state, resume_assets_from=args.resume_assets_from
                )
                print(
                    json.dumps(
                        {"status": result["status"], "state_path": str(args.state)},
                        ensure_ascii=False,
                    )
                )
            else:
                print(
                    json.dumps(
                        {"plan": package.describe(), "preflight": preflight(api)},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        return 0
    except DeploymentError as error:
        print(str(error))
        return 1
    except OSError:
        print("Local file operation failed; inspect paths and saved state before retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
