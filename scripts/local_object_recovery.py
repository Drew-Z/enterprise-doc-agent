"""Private local object capture and loopback-only restore; no source writes.

Callers supply S3 clients with bounded connect/read timeouts and retries. Output
must be inside a restricted private directory outside the repository. The caller
owns creation and cleanup of the dedicated local object-store process and buckets.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from enterprise_doc_core.recovery.object_store import SHA256_PATTERN, ObjectReference


class LocalObjectRecoveryError(RuntimeError):
    """Safe operational error without source keys, payloads or credentials."""


def _filename(bucket: str, key: str) -> str:
    return hashlib.sha256(f"{bucket}\0{key}".encode()).hexdigest() + ".blob"


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _limits(workers: int, timeout_seconds: float) -> float:
    if not 1 <= workers <= 4 or not 0 < timeout_seconds <= 1800:
        raise LocalObjectRecoveryError("invalid worker count or operation deadline")
    return time.monotonic() + timeout_seconds


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise LocalObjectRecoveryError("object operation deadline exceeded")


def _consume(client: Any, item: dict[str, Any], handle: Any, deadline: float) -> None:
    _check_deadline(deadline)
    response = client.get_object(Bucket=item["bucket"], Key=item["key"])
    body = response["Body"]
    try:
        if response.get("ContentLength") != item["size_bytes"]:
            raise LocalObjectRecoveryError("object size does not match database metadata")
        size = 0
        digest = hashlib.sha256()
        while True:
            _check_deadline(deadline)
            block = body.read(min(1024 * 1024, item["size_bytes"] - size + 1))
            if not block:
                break
            size += len(block)
            if size > item["size_bytes"]:
                raise LocalObjectRecoveryError("object exceeds its declared size")
            digest.update(block)
            if handle is not None:
                handle.write(block)
        if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
            raise LocalObjectRecoveryError("object bytes do not match database metadata")
    finally:
        body.close()


def capture_objects(
    *,
    client: Any,
    references: Iterable[ObjectReference],
    output_dir: Path,
    allowed_buckets: frozenset[str],
    max_objects: int,
    max_total_bytes: int,
    workers: int = 1,
    timeout_seconds: float = 600,
) -> dict[str, Any]:
    """Download an immutable, DB-bound local snapshot. Failures retain partial files."""
    deadline = _limits(workers, timeout_seconds)
    root = output_dir.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise LocalObjectRecoveryError("private snapshots must be outside the repository")
    if root.exists():
        raise LocalObjectRecoveryError("snapshot output already exists")
    if not allowed_buckets or max_objects <= 0 or max_total_bytes < 0:
        raise LocalObjectRecoveryError("invalid object budget or bucket allowlist")
    objects: dict[tuple[str, str], dict[str, Any]] = {}
    identities: set[tuple[str, str]] = set()
    total = 0
    for ref in references:
        identity = (ref.reference_type, str(ref.reference_id))
        if (
            ref.bucket not in allowed_buckets
            or not ref.key
            or ref.reference_type not in {"document_version", "agent_artifact"}
            or identity in identities
            or ref.size_bytes < 0
            or SHA256_PATTERN.fullmatch(ref.sha256) is None
        ):
            raise LocalObjectRecoveryError("invalid or duplicate database object reference")
        identities.add(identity)
        location = (ref.bucket, ref.key)
        if location not in objects:
            total += ref.size_bytes
            if len(objects) >= max_objects or total > max_total_bytes:
                raise LocalObjectRecoveryError("snapshot exceeds the approved object budget")
            objects[location] = {
                "bucket": ref.bucket,
                "key": ref.key,
                "size_bytes": ref.size_bytes,
                "sha256": ref.sha256,
                "file": _filename(ref.bucket, ref.key),
                "references": [],
            }
        item = objects[location]
        if (item["size_bytes"], item["sha256"]) != (ref.size_bytes, ref.sha256):
            raise LocalObjectRecoveryError("database references disagree on object integrity")
        item["references"].append(
            {"reference_type": ref.reference_type, "reference_id": str(ref.reference_id)}
        )
    if not objects:
        raise LocalObjectRecoveryError("snapshot has no durable object references")
    ordered = [objects[key] for key in sorted(objects)]
    try:
        root.mkdir(mode=0o700, parents=False, exist_ok=False)

        def download(item: dict[str, Any]) -> None:
            path = root / item["file"]
            with path.open("xb") as handle:
                path.chmod(0o600)
                _consume(client, item, handle, deadline)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(download, ordered))
        payload = {"schema_version": 1, "operation": "local-object-snapshot", "objects": ordered}
        manifest = root / "objects.json"
        with manifest.open("xb") as handle:
            manifest.chmod(0o600)
            handle.write(
                (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            )
        return {
            "status": "passed",
            "object_count": len(ordered),
            "reference_count": len(identities),
            "size_bytes": total,
            "manifest_sha256": _digest(manifest),
        }
    except LocalObjectRecoveryError:
        raise
    except Exception:
        raise LocalObjectRecoveryError(
            "local object capture failed; retain private evidence"
        ) from None


def restore_objects(
    *,
    client: Any,
    manifest_path: Path,
    expected_sha256: str,
    allowed_buckets: frozenset[str],
    workers: int = 1,
    timeout_seconds: float = 600,
) -> dict[str, Any]:
    """Verify all files first, then restore original keys into empty loopback buckets."""
    deadline = _limits(workers, timeout_seconds)
    endpoint = urlsplit(client.meta.endpoint_url)
    if endpoint.scheme not in {"http", "https"} or endpoint.hostname not in {"127.0.0.1", "::1"}:
        raise LocalObjectRecoveryError("object restore requires an explicit loopback endpoint")
    root = manifest_path.resolve().parent
    try:
        if (
            manifest_path.stat().st_size > 64 * 1024 * 1024
            or _digest(manifest_path) != expected_sha256
        ):
            raise LocalObjectRecoveryError("snapshot manifest does not match the expected SHA-256")
        payload = json.loads(manifest_path.read_bytes())
        if payload["schema_version"] != 1 or payload["operation"] != "local-object-snapshot":
            raise LocalObjectRecoveryError("unsupported local snapshot manifest")
        objects = payload["objects"]
        if not isinstance(objects, list) or not objects:
            raise LocalObjectRecoveryError("snapshot has no objects")
        seen = set()
        for item in objects:
            identity = (item["bucket"], item["key"])
            if (
                item["bucket"] not in allowed_buckets
                or not item["key"]
                or identity in seen
                or item["file"] != _filename(*identity)
                or SHA256_PATTERN.fullmatch(item["sha256"]) is None
                or not isinstance(item["size_bytes"], int)
                or item["size_bytes"] < 0
            ):
                raise LocalObjectRecoveryError("snapshot contains an invalid object mapping")
            seen.add(identity)
            path = root / item["file"]
            if (
                path.resolve().parent != root
                or path.is_symlink()
                or path.stat().st_size != item["size_bytes"]
                or _digest(path) != item["sha256"]
            ):
                raise LocalObjectRecoveryError("local object does not match the snapshot manifest")
        for bucket in sorted({item["bucket"] for item in objects}):
            listing = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
            if listing.get("Contents") or listing.get("IsTruncated"):
                raise LocalObjectRecoveryError("restore target bucket must be empty")

        def upload(item: dict[str, Any]) -> None:
            _check_deadline(deadline)
            with (root / item["file"]).open("rb") as handle:
                # Recheck the same open descriptor that will supply the upload bytes.
                if hashlib.file_digest(handle, "sha256").hexdigest() != item["sha256"]:
                    raise LocalObjectRecoveryError("local object changed after validation")
                handle.seek(0)
                client.put_object(
                    Bucket=item["bucket"], Key=item["key"], Body=handle, IfNoneMatch="*"
                )
            _consume(client, item, None, deadline)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(upload, objects))
        return {
            "status": "passed",
            "verified_objects": len(objects),
            "manifest_sha256": expected_sha256,
        }
    except LocalObjectRecoveryError:
        raise
    except Exception:
        raise LocalObjectRecoveryError(
            "local object restore failed; retain private evidence"
        ) from None
