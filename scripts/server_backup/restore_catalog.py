"""Local operator evidence feeds retention; upload markers cannot certify restores.

Expected hashes are trust anchors supplied from the protected recovery registry,
not values taken from these documents or from object storage. This module writes
no files, uploads nothing, and never deletes. Keep its catalog/evidence local.
"""

import datetime
import hashlib
import json
import re

from .recovery_bundle import MAX_BYTES, open_bundle
from .remote_retention import MAX_KEYS, SHA, SNAPSHOT, plan_retention

MAX_CATALOG_BYTES = 8 * 1024 * 1024


class CatalogError(ValueError):
    """Invalid or unbound local evidence, without private contents in the error."""


def _require(value):
    if not value:
        raise CatalogError("recovery catalog evidence is invalid or unbound")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _sid(value):
    return isinstance(value, str) and re.fullmatch(SNAPSHOT, value) is not None


def _pairs(items):
    value = {}
    for key, item in items:
        _require(key not in value)
        value[key] = item
    return value


def _parse(raw, *, maximum=MAX_CATALOG_BYTES):
    _require(isinstance(raw, bytes) and 1 <= len(raw) <= maximum)
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _require(False))
    except (ValueError, TypeError, RecursionError):
        raise CatalogError("recovery evidence JSON is invalid") from None


def _bound(raw, expected, *, maximum=MAX_CATALOG_BYTES):
    _require(_hash(expected) and isinstance(raw, bytes) and len(raw) <= maximum)
    _require(_sha(raw) == expected)


def _time(value):
    try:
        stamp = datetime.datetime.fromisoformat(value)
        _require(stamp.utcoffset() == datetime.timedelta(0))
        return stamp
    except (ValueError, TypeError):
        raise CatalogError("recovery evidence timestamp is invalid") from None


def inspect_snapshot(*, snapshot_id, ciphertext, expected_sha256, age, identity):
    """Authenticate a local bundle and derive content identity, not a restore claim."""
    _require(_sid(snapshot_id))
    _bound(ciphertext, expected_sha256, maximum=MAX_BYTES)
    recovered = open_bundle(age=age, identity=identity, ciphertext=ciphertext)
    try:
        payload, metadata = recovered["payload"], recovered["metadata"]
        source = _parse(payload["source-inventory.json"])
        objects = _parse(payload["objects.json"])["objects"]
        release = _parse(payload["release.json"])
        tables = source["inventory"]
        _require(isinstance(tables, list) and 1 <= len(tables) <= 100)
        names = set()
        for row in tables:
            _require(set(row) == {"table", "rows", "sha256"})
            _require(
                isinstance(row["table"], str)
                and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", row["table"])
            )
            _require(row["table"] not in names and type(row["rows"]) is int and row["rows"] >= 0)
            _require(_hash(row["sha256"]))
            names.add(row["table"])
        _require(sorted(source["tables"]) == sorted(names) and source["read_only"] == "on")
        _require(source["dump_sha256"] == _sha(payload["database.dump"]))
        _require(
            type(source["dump_size_bytes"]) is int
            and source["dump_size_bytes"] == len(payload["database.dump"])
        )
        _require(metadata["database_sha256"] == source["dump_sha256"])
        _require(metadata["captured_at"] == source["captured_at"])
        _time(source["captured_at"])
        _require(isinstance(objects, list) and 1 <= len(objects) <= MAX_KEYS)
        mappings, locations, references = [], set(), set()
        members = {"database.dump", "source-inventory.json", "objects.json", "release.json"}
        for item in objects:
            _require(
                set(item)
                == {
                    "reference_type",
                    "reference_id",
                    "bucket",
                    "key",
                    "size_bytes",
                    "sha256",
                    "bundle_member",
                }
            )
            _require(
                all(
                    isinstance(item[k], str) and 0 < len(item[k]) <= 2048
                    for k in ("reference_type", "reference_id", "bucket", "key")
                )
            )
            _require(item["reference_type"] in {"document_version", "agent_artifact"})
            location = (item["bucket"], item["key"])
            reference = (item["reference_type"], item["reference_id"])
            _require(location not in locations and reference not in references)
            locations.add(location)
            references.add(reference)
            member = "objects/" + _sha((item["bucket"] + "\0" + item["key"]).encode()) + ".blob"
            _require(item["bundle_member"] == member)
            raw = payload[member]
            _require(type(item["size_bytes"]) is int and item["size_bytes"] == len(raw))
            _require(item["sha256"] == _sha(raw))
            members.add(member)
            mappings.append({k: v for k, v in item.items() if k != "bundle_member"})
        _require(set(payload) == members and isinstance(release, dict) and bool(release))
        _require(isinstance(source["revisions"], list) and bool(source["revisions"]))
        _require(all(isinstance(v, str) and 0 < len(v) <= 100 for v in source["revisions"]))
        _require(isinstance(source["extensions"], list))
        content = {
            "table_fingerprints_sha256": _sha(_json(sorted(tables, key=lambda r: r["table"]))),
            "object_mappings_sha256": _sha(
                _json(sorted(mappings, key=lambda r: (r["bucket"], r["key"])))
            ),
            "schema_sha256": _sha(
                _json(
                    {
                        "revisions": sorted(source["revisions"]),
                        "extensions": sorted(source["extensions"], key=_json),
                    }
                )
            ),
            "release_sha256": _sha(_json(release)),
        }
        return {
            "snapshot_id": snapshot_id,
            "ciphertext_sha256": expected_sha256,
            "database_sha256": source["dump_sha256"],
            "captured_at": source["captured_at"],
            "content": content,
            "content_sha256": _sha(_json(content)),
            "tables": len(tables),
            "rows": sum(r["rows"] for r in tables),
            "objects": len(objects),
            "object_bytes": sum(r["size_bytes"] for r in objects),
        }
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise CatalogError("authenticated source inventory is invalid") from None


def _source(value):
    _require(
        isinstance(value, dict)
        and set(value)
        == {
            "snapshot_id",
            "ciphertext_sha256",
            "database_sha256",
            "captured_at",
            "content",
            "content_sha256",
            "tables",
            "rows",
            "objects",
            "object_bytes",
        }
    )
    _require(_sid(value["snapshot_id"]))
    _require(
        all(_hash(value[k]) for k in ("ciphertext_sha256", "database_sha256", "content_sha256"))
    )
    content = value["content"]
    _require(
        isinstance(content, dict)
        and set(content)
        == {
            "table_fingerprints_sha256",
            "object_mappings_sha256",
            "schema_sha256",
            "release_sha256",
        }
    )
    _require(
        all(_hash(v) for v in content.values()) and value["content_sha256"] == _sha(_json(content))
    )
    _require(
        all(
            type(value[k]) is int and value[k] >= 0
            for k in ("tables", "rows", "objects", "object_bytes")
        )
    )
    _require(1 <= value["tables"] <= 100 and 1 <= value["objects"] <= MAX_KEYS)
    _time(value["captured_at"])


def build_catalog(*, snapshots, references, pinned_ids, in_progress_ids):
    """Build a bounded immutable document for exclusive storage by the operator."""
    value = {
        "schema_version": 1,
        "snapshots": snapshots,
        "references": references,
        "pinned_ids": sorted(pinned_ids),
        "in_progress_ids": sorted(in_progress_ids),
    }
    _inputs(value)
    _require(len(_json(value)) <= MAX_CATALOG_BYTES)
    return value


def record_restore(
    *, source, report_bytes, expected_report_sha256, verifier_bytes, expected_verifier_sha256
):
    """Import an operator-pinned executed verifier receipt, including failures.

    A digest proves identity, not that code executed. The operator must select
    these two anchors from the original execution record in the protected local
    registry. Neither the uploader nor a completion marker supplies them.
    """
    _source(source)
    _bound(report_bytes, expected_report_sha256, maximum=128 * 1024)
    _bound(verifier_bytes, expected_verifier_sha256, maximum=128 * 1024)
    result = {
        "report": _parse(report_bytes, maximum=128 * 1024),
        "original_report": report_bytes.decode("utf-8"),
        "evidence_sha256": expected_report_sha256,
        "verifier_sha256": expected_verifier_sha256,
    }
    _attempt(source, result)
    return result


def _attempt(source, attempt):
    _require(
        isinstance(attempt, dict)
        and set(attempt) == {"report", "original_report", "evidence_sha256", "verifier_sha256"}
    )
    _require(isinstance(attempt["original_report"], str) and _hash(attempt["verifier_sha256"]))
    raw = attempt["original_report"].encode("utf-8")
    _bound(raw, attempt["evidence_sha256"], maximum=128 * 1024)
    report = _parse(raw, maximum=128 * 1024)
    _require(report == attempt["report"] and isinstance(report, dict))
    status = report.get("status")
    _require(isinstance(status, str) and 1 <= len(status) <= 128)
    # Even partial/failed evidence must not be transplanted from another source.
    for field, expected in (
        ("source_snapshot", source["snapshot_id"]),
        ("ciphertext_sha256", source["ciphertext_sha256"]),
    ):
        if field in report:
            _require(report[field] == expected)
    if status != "ciphertext_database_and_objects_restored":
        return None
    try:
        _require(report["source_snapshot"] == source["snapshot_id"])
        _require(report["ciphertext_sha256"] == source["ciphertext_sha256"])
        _require(
            _time(source["captured_at"]) <= _time(report["at"]) <= _time(report["completed_at"])
        )
        _require(report["original_snapshot_read_during_restore"] is False)
        _require(report["plaintext_host_archive_written"] is False)
        _require(report["original_containers_still_running"] is True and "error_type" not in report)
        database, objects = report["database"], report["objects"]
        _require(
            database
            == {
                "tables": source["tables"],
                "rows": source["rows"],
                "all_fingerprints_match": True,
                "archive_sha256": source["database_sha256"],
            }
        )
        _require(
            objects
            == {
                "count": source["objects"],
                "bytes": source["object_bytes"],
                "all_readback_bytes_match": True,
                "inventory_exact": True,
            }
        )
        _require(
            database["all_fingerprints_match"] is True
            and objects["all_readback_bytes_match"] is True
            and objects["inventory_exact"] is True
        )
        _require(
            all(
                type(v) is int
                for v in (database["tables"], database["rows"], objects["count"], objects["bytes"])
            )
        )
        cleanup = report["cleanup"]
        _require(isinstance(cleanup, list) and len(cleanup) == 2)
        _require(
            all(
                set(r) == {"id", "removed"} and _hash(r["id"]) and r["removed"] is True
                for r in cleanup
            )
        )
        _require(len({r["id"] for r in cleanup}) == 2)
    except (KeyError, TypeError, ValueError):
        raise CatalogError(
            "successful restore receipt does not establish the required checks"
        ) from None
    return {
        "status": "actual_restore_verified",
        "ciphertext_sha256": source["ciphertext_sha256"],
        "content_sha256": source["content_sha256"],
        "evidence_sha256": attempt["evidence_sha256"],
        "database_fingerprints_match": True,
        "object_bytes_match": True,
    }


def _inputs(value):
    _require(
        isinstance(value, dict)
        and set(value)
        == {"schema_version", "snapshots", "references", "pinned_ids", "in_progress_ids"}
    )
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    snapshots = value["snapshots"]
    _require(isinstance(snapshots, list) and len(snapshots) <= MAX_KEYS)
    protected = set()
    for key in ("pinned_ids", "in_progress_ids"):
        ids = value[key]
        _require(isinstance(ids, list) and len(ids) <= MAX_KEYS and all(_sid(sid) for sid in ids))
        protected.update(ids)
    refs = value["references"]
    _require(isinstance(refs, dict) and len(refs) <= MAX_KEYS)
    for owner, ids in refs.items():
        _require(isinstance(owner, str) and 1 <= len(owner) <= 256)
        _require(
            isinstance(ids, list) and 1 <= len(ids) <= MAX_KEYS and all(_sid(sid) for sid in ids)
        )
        protected.update(ids)
    seen, proofs = set(), {}
    for row in snapshots:
        _require(isinstance(row, dict) and set(row) == {"source", "attempts"})
        source, attempts = row["source"], row["attempts"]
        _source(source)
        sid = source["snapshot_id"]
        _require(sid not in seen)
        seen.add(sid)
        _require(isinstance(attempts, list) and len(attempts) <= 128)
        evidence = set()
        successful = []
        for attempt in attempts:
            proof = _attempt(source, attempt)
            _require(attempt["evidence_sha256"] not in evidence)
            evidence.add(attempt["evidence_sha256"])
            if proof is None:
                protected.add(sid)
            else:
                successful.append((_time(attempt["report"]["completed_at"]), proof))
        if successful:
            proofs[sid] = max(successful, key=lambda p: (p[0], p[1]["evidence_sha256"]))[1]
        else:
            protected.add(sid)
    return proofs, protected


def retention_inputs(raw, *, expected_sha256):
    """An independently pinned local catalog is mandatory; fail closed on drift."""
    _bound(raw, expected_sha256)
    return _inputs(_parse(raw))


def plan_from_catalog(*, catalog_bytes, expected_catalog_sha256, **kwargs):
    proofs, protected = retention_inputs(catalog_bytes, expected_sha256=expected_catalog_sha256)
    return plan_retention(restore_proofs=proofs, protected_ids=protected, **kwargs)
