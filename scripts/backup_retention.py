"""Plan verified scheduled-backup retirement; mutation belongs to the locked operator."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_NAME = re.compile(r"backup-tick-[0-9]{8}-[0-9]{6}-[0-9]{6}")
_FILES = {
    "intent.json",
    "source-inventory.raw.json",
    "source-inventory.json",
    "database.dump",
    "owned-container.json",
    "objects.private.json",
    "object-references.private.json",
    "receipt.json",
    "cleanup.json",
}


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _paths(value: Any, root: Path) -> list[Path]:
    if isinstance(value, dict):
        return [p for v in value.values() for p in _paths(v, root)]
    if isinstance(value, list):
        return [p for v in value for p in _paths(v, root)]
    if isinstance(value, str) and value.replace("\\", "/").casefold().startswith(
        str(root).replace("\\", "/").casefold() + "/"
    ):
        return [Path(value).resolve()]
    return []


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink() or path.is_junction():
        raise ValueError("recovery_path_rejected")
    return resolved


def plan_retention(group: Path, *, keep: int = 5) -> dict[str, Any]:
    if type(keep) is not int or keep < 5:
        raise ValueError("retention_floor_rejected")
    group = group.resolve()
    stage = _inside(group / "final-delivery-evidence", group)
    manifest_path = group / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    records = manifest["final_delivery_phase"]["scheduled_backup_records"]
    if (
        not isinstance(records, dict)
        or not (group / "final-delivery-artifact-index.json").is_file()
    ):
        raise ValueError("recovery_registry_required")
    protected_paths: list[Path] = []
    for index in group.glob("*artifact-index*.json"):
        protected_paths.extend(_paths(json.loads(index.read_bytes()), stage))
    referenced_paths: dict[str, Path] = {}
    for record in records.values():
        if record["status"] == "retired":
            continue
        path = record.get("objects_manifest_path")
        if path is None:
            continue
        objects = _inside(Path(path), group)
        if _sha(objects) != record["objects_manifest_sha256"]:
            raise ValueError("object_manifest_changed")
        for item in json.loads(objects.read_bytes())["objects"]:
            raw_path = item["backup_path"]
            if raw_path not in referenced_paths:
                referenced_paths[raw_path] = _inside(Path(raw_path), group)
    eligible = []
    retained = []
    for name, record in records.items():
        if record["status"] == "retired":
            continue
        directory = stage / name
        reason = None
        if not _NAME.fullmatch(name):
            reason = "outside_scheduled_scope"
        elif record.get("pinned") is not False or record["status"] != "verified":
            reason = "pinned_or_unverified"
        else:
            _inside(directory, stage)
            if any(p.is_relative_to(directory) for p in protected_paths):
                reason = "frozen_evidence"
            elif any(p.is_relative_to(directory) for p in referenced_paths.values()):
                reason = "referenced_object_bytes"
            elif not (directory / "cleanup.json").is_file():
                reason = "cleanup_not_verified"
        if reason:
            retained.append({"directory": str(directory), "reason": reason})
            continue
        created = datetime.fromisoformat(record["created_at"])
        if created.tzinfo is None:
            raise ValueError("snapshot_timestamp_rejected")
        eligible.append((created, name, record))
    eligible.sort(reverse=True)
    recent_hashes: set[str] = set()
    candidates = []
    for _, name, record in eligible:
        directory = stage / name
        digest = record["database_sha256"]
        database = _inside(Path(record["database_path"]), directory)
        if database != directory / "database.dump" or _sha(database) != digest:
            raise ValueError("database_changed")
        receipt = json.loads((directory / "receipt.json").read_bytes())
        if receipt["status"] != "passed" or receipt["database_sha256"] != digest:
            raise ValueError("snapshot_not_verified")
        if len(recent_hashes) < keep and digest not in recent_hashes:
            recent_hashes.add(digest)
            retained.append({"directory": str(directory), "reason": "recent_distinct_snapshot"})
            continue
        files = []
        for path in sorted(directory.iterdir()):
            _inside(path, directory)
            if not path.is_file() or path.name not in _FILES:
                raise ValueError("unexpected_snapshot_contents")
            files.append({"path": str(path), "sha256": _sha(path), "bytes": path.stat().st_size})
        candidates.append(
            {
                "name": name,
                "directory": str(directory),
                "files": files,
                "reason": "older_verified_unreferenced_snapshot",
            }
        )
    plan = {
        "group": str(group),
        "keep_distinct": keep,
        "manifest_sha256": _sha(manifest_path),
        "candidates": list(reversed(candidates)),
        "retained": retained,
    }
    plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan
