"""Bound one writer's remote namespace; retention produces reviewable plans only.

The process holding backup_runtime.runtime_lock is the sole namespace writer.
This module never deletes remote objects, even when the byte cap is reached.
Actual restore proofs and protection references come from the trusted operator
catalog, never from an upload marker's claims. Unproven snapshots stay protected.
"""

import datetime
import hashlib
import json
import re

from .server_publication import (
    MAX_CIPHERTEXT_BYTES,
    PublicationError,
    marker_bytes,
    publish_snapshot,
    snapshot_record,
)

PREFIX = "operations-recovery/v1/"
DEFAULT_MAX_BYTES = 3 * 1024**3
MAX_KEYS = 4096
MAX_MARKER_BYTES = 4096
SNAPSHOT = r"snapshot-[a-z0-9-]{1,80}"
KEY = re.compile(r"(" + SNAPSHOT + r")(\.tar\.age|\.complete\.json)")
SHA = re.compile(r"[0-9a-f]{64}")


class RemoteInventoryError(PublicationError):
    """Remote inventory cannot safely be used for admission or retention."""


class RemoteBudgetExceeded(PublicationError):
    """Preserve existing remote recovery points and leave sealed upload pending."""


def inventory(*, client, bucket, prefix):
    if prefix != PREFIX:
        raise RemoteInventoryError("unapproved inventory prefix")
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise RemoteInventoryError("invalid inventory bucket")
    objects = {}
    seen_tokens = set()
    token = None
    for _ in range(6):
        args = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token is not None:
            args["ContinuationToken"] = token
        page = client.list_objects_v2(**args)
        rows = page.get("Contents", [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise RemoteInventoryError("invalid inventory page")
        for row in rows:
            key, size, etag = row.get("Key"), row.get("Size"), row.get("ETag")
            match = (
                KEY.fullmatch(key[len(prefix) :])
                if isinstance(key, str) and key.startswith(prefix)
                else None
            )
            if (
                match is None
                or key in objects
                or type(size) is not int
                or size < 0
                or not isinstance(etag, str)
                or not 1 <= len(etag) <= 256
            ):
                raise RemoteInventoryError("unknown or inconsistent remote object")
            objects[key] = {
                "key": key,
                "bytes": size,
                "etag": etag,
                "snapshot_id": match[1],
                "kind": match[2],
            }
            if len(objects) > MAX_KEYS:
                raise RemoteInventoryError("remote inventory exceeds key budget")
        truncated = page.get("IsTruncated", False)
        if type(truncated) is not bool:
            raise RemoteInventoryError("invalid pagination state")
        if not truncated:
            return objects
        token = page.get("NextContinuationToken")
        if not isinstance(token, str) or not token or token in seen_tokens or len(token) > 8192:
            raise RemoteInventoryError("invalid inventory continuation")
        seen_tokens.add(token)
    raise RemoteInventoryError("remote inventory exceeds page budget")


def budget_admission(*, objects, expected, max_bytes):
    if type(max_bytes) is not int or not 1 <= max_bytes <= DEFAULT_MAX_BYTES:
        raise RemoteInventoryError("invalid remote byte cap")
    for key, size in expected.items():
        if key in objects and objects[key]["bytes"] != size:
            raise RemoteInventoryError("pending identity has a different remote size")
    current = sum(row["bytes"] for row in objects.values())
    additional = sum(size for key, size in expected.items() if key not in objects)
    if additional and current + additional > max_bytes:
        raise RemoteBudgetExceeded("remote byte cap reached; existing snapshots retained")
    return {
        "current_bytes": current,
        "additional_bytes": additional,
        "projected_bytes": current + additional,
        "max_bytes": max_bytes,
    }


def publish_budgeted_snapshot(
    *, client, bucket, prefix, max_bytes=DEFAULT_MAX_BYTES, multipart_journal=None, **kwargs
):
    record = snapshot_record(bucket=bucket, prefix=prefix, **kwargs)
    expected = {
        record["ciphertext_key"]: record["ciphertext_bytes"],
        prefix + record["snapshot_id"] + ".complete.json": len(marker_bytes(record)),
    }
    objects = inventory(client=client, bucket=bucket, prefix=prefix)
    budget_admission(objects=objects, expected=expected, max_bytes=max_bytes)
    return publish_snapshot(
        client=client,
        bucket=bucket,
        prefix=prefix,
        max_bytes=max_bytes,
        multipart_journal=multipart_journal,
        **kwargs,
    )


def _utc(value):
    stamp = datetime.datetime.fromisoformat(value)
    if stamp.utcoffset() != datetime.timedelta(0):
        raise ValueError("snapshot timestamp is not UTC")
    return stamp


def _read_marker(client, bucket, row):
    if not 1 <= row["bytes"] <= MAX_MARKER_BYTES:
        raise ValueError("marker size outside budget")
    response = client.get_object(Bucket=bucket, Key=row["key"], IfMatch=row["etag"])
    body = response["Body"]
    try:
        if response.get("ContentLength") != row["bytes"] or response.get("ETag") != row["etag"]:
            raise ValueError("marker changed during planning")
        raw = body.read(row["bytes"] + 1)
    finally:
        body.close()
    if len(raw) != row["bytes"]:
        raise ValueError("marker length differs")
    return raw, json.loads(raw)


def _proof_matches(proof, marker):
    # This is a trusted input boundary, not an inference from upload success.
    return (
        isinstance(proof, dict)
        and proof.get("status") == "actual_restore_verified"
        and proof.get("ciphertext_sha256") == marker["ciphertext_sha256"]
        and proof.get("database_fingerprints_match") is True
        and proof.get("object_bytes_match") is True
        and isinstance(proof.get("content_sha256"), str)
        and SHA.fullmatch(proof["content_sha256"]) is not None
        and isinstance(proof.get("evidence_sha256"), str)
        and SHA.fullmatch(proof["evidence_sha256"]) is not None
    )


def plan_retention(
    *,
    client,
    bucket,
    prefix,
    restore_proofs,
    protected_ids,
    max_bytes=DEFAULT_MAX_BYTES,
    keep_distinct=5,
    at=None,
):
    """Return exact candidate keys and hashes; no deletion or approval inferred.

    keep_distinct cannot be weakened below the user's five-content requirement.
    Legacy / unverified captures count as independent unknown content and remain.
    Failed or referenced snapshots are provided as protected_ids by their owner.
    """
    if type(keep_distinct) is not int or not 5 <= keep_distinct <= MAX_KEYS:
        raise RemoteInventoryError("at least five distinct contents must be retained")
    if not isinstance(restore_proofs, dict) or not isinstance(protected_ids, (set, frozenset)):
        raise RemoteInventoryError("explicit trusted recovery catalog required")
    if any(not isinstance(sid, str) or not re.fullmatch(SNAPSHOT, sid) for sid in protected_ids):
        raise RemoteInventoryError("invalid protection identity")
    observed = datetime.datetime.now(datetime.UTC) if at is None else _utc(at)
    objects = inventory(client=client, bucket=bucket, prefix=prefix)
    budget = budget_admission(objects=objects, expected={}, max_bytes=max_bytes)
    grouped = {}
    for row in objects.values():
        grouped.setdefault(row["snapshot_id"], {})[row["kind"]] = row
    snapshots = []
    for sid, pair in grouped.items():
        entry = {
            "snapshot_id": sid,
            "objects": sorted(pair.values(), key=lambda r: r["key"]),
            "bytes": sum(r["bytes"] for r in pair.values()),
            "reasons": [],
        }
        if sid in protected_ids:
            entry["reasons"].append("pinned_referenced_failed_or_in_progress")
        if set(pair) != {".tar.age", ".complete.json"}:
            entry["reasons"].append("incomplete_or_unknown_publication")
            snapshots.append(entry)
            continue
        try:
            raw, marker = _read_marker(client, bucket, pair[".complete.json"])
            if (
                not isinstance(marker, dict)
                or marker.get("schema_version") != 1
                or marker.get("snapshot_id") != sid
                or marker.get("status") != "ciphertext_upload_readback_verified"
                or marker.get("ciphertext_key") != pair[".tar.age"]["key"]
                or type(marker.get("ciphertext_bytes")) is not int
                or marker["ciphertext_bytes"] != pair[".tar.age"]["bytes"]
                or not 32 <= marker["ciphertext_bytes"] <= MAX_CIPHERTEXT_BYTES
                or not isinstance(marker.get("ciphertext_sha256"), str)
                or SHA.fullmatch(marker["ciphertext_sha256"]) is None
            ):
                raise ValueError("publication marker differs from inventory")
            captured_at = _utc(marker["captured_at"])
            if captured_at > observed:
                raise ValueError("future source time")
            entry.update(
                captured_at=captured_at.isoformat(),
                ciphertext_sha256=marker["ciphertext_sha256"],
                marker_sha256=hashlib.sha256(raw).hexdigest(),
            )
        except (ValueError, TypeError, KeyError):
            entry["reasons"].append("invalid_or_changed_marker")
            snapshots.append(entry)
            continue
        proof = restore_proofs.get(sid)
        if _proof_matches(proof, marker):
            entry["content_sha256"] = proof["content_sha256"]
            entry["restore_evidence_sha256"] = proof["evidence_sha256"]
        else:
            entry["reasons"].append("actual_restore_unverified")
        snapshots.append(entry)
    complete = sorted(
        (r for r in snapshots if "content_sha256" in r),
        key=lambda r: (r["captured_at"], r["snapshot_id"]),
        reverse=True,
    )
    retained_contents = set()
    for entry in complete:
        content = entry["content_sha256"]
        if content not in retained_contents and len(retained_contents) < keep_distinct:
            retained_contents.add(content)
            entry["reasons"].append("latest_distinct_content")
    candidates = [r for r in snapshots if not r["reasons"]]
    retained = [r for r in snapshots if r["reasons"]]
    result = {
        "schema_version": 1,
        "status": "retention_plan_requires_explicit_approval",
        "bucket": bucket,
        "prefix": prefix,
        "observed_at": observed.isoformat(),
        "max_bytes": max_bytes,
        "current_bytes": budget["current_bytes"],
        "over_budget": budget["current_bytes"] > max_bytes,
        "keep_distinct": keep_distinct,
        "candidate_bytes": sum(r["bytes"] for r in candidates),
        "candidates": sorted(candidates, key=lambda r: r["snapshot_id"]),
        "retained": sorted(retained, key=lambda r: r["snapshot_id"]),
        "missing_protected_ids": sorted(protected_ids - grouped.keys()),
        "remote_mutations": 0,
        "approval_granted": False,
    }
    result["plan_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result
