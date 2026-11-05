"""Exercise the operator catalog; only age/process and S3 boundaries are replaced."""

import hashlib
import json
from unittest.mock import patch

import pytest
from scripts.server_backup import recovery_bundle


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle(
    tmp_path,
    *,
    stamp="2026-10-02T08:00:00+00:00",
    dump=b"PGDMP-test",
    table=b"table rows",
    blob=b"source object",
    release="rc.16",
):
    member = "objects/" + sha(b"documents\0test.txt") + ".blob"
    inventory = {
        "captured_at": stamp,
        "snapshot": "0001-0002-1",
        "read_only": "on",
        "tables": ["documents"],
        "inventory": [{"table": "documents", "rows": 2, "sha256": sha(table)}],
        "dump_sha256": sha(dump),
        "dump_size_bytes": len(dump),
        "revisions": ["20260924_0031"],
        "extensions": [{"name": "vector", "version": "0.8.0", "schema": "public"}],
    }
    objects = [
        {
            "reference_type": "document_version",
            "reference_id": "test-version",
            "bucket": "documents",
            "key": "test.txt",
            "size_bytes": len(blob),
            "sha256": sha(blob),
            "bundle_member": member,
        }
    ]
    payload = {
        "database.dump": dump,
        "source-inventory.json": encode(inventory),
        "objects.json": encode({"objects": objects}),
        member: blob,
        "release.json": encode({"source_sha": "a" * 40, "version": release}),
    }
    identity = tmp_path / "identity"
    identity.write_text("synthetic process boundary")

    # The subprocess substitutes authenticated encryption only. The actual bundle
    # parser, exact member/hash checks and catalog all execute normally.
    def process(args, *, input, **kwargs):
        from types import SimpleNamespace

        header = b"age-encryption.org/v1\n"
        return SimpleNamespace(
            returncode=0, stdout=(input[len(header) :] if "--decrypt" in args else header + input)
        )

    with patch("subprocess.run", side_effect=process):
        raw = recovery_bundle.seal_bundle(
            age="synthetic-age",
            recipient="age1" + "q" * 58,
            payload=payload,
            metadata={"captured_at": stamp, "database_sha256": sha(dump)},
        )
    return raw, identity, process


def test_unrestored_snapshot_is_protected_and_catalog_requires_exact_anchor(tmp_path):
    from scripts.server_backup import restore_catalog as catalog

    raw, identity, process = bundle(tmp_path)
    with patch("subprocess.run", side_effect=process):
        source = catalog.inspect_snapshot(
            snapshot_id="snapshot-test-1",
            ciphertext=raw,
            expected_sha256=sha(raw),
            age="synthetic-age",
            identity=identity,
        )
    document = catalog.build_catalog(
        snapshots=[{"source": source, "attempts": []}],
        references={},
        pinned_ids=set(),
        in_progress_ids=set(),
    )
    restored, protected = catalog.retention_inputs(
        encode(document), expected_sha256=sha(encode(document))
    )
    assert restored == {}
    assert protected == {"snapshot-test-1"}


def inspect(tmp_path, **kwargs):
    from scripts.server_backup import restore_catalog as catalog

    raw, identity, process = bundle(tmp_path, **kwargs)
    with patch("subprocess.run", side_effect=process):
        return catalog.inspect_snapshot(
            snapshot_id="snapshot-test-1",
            ciphertext=raw,
            expected_sha256=sha(raw),
            age="synthetic-age",
            identity=identity,
        )


def report(source):
    return {
        "status": "ciphertext_database_and_objects_restored",
        "source_snapshot": source["snapshot_id"],
        "ciphertext_sha256": source["ciphertext_sha256"],
        "at": "2026-10-02T09:00:00+00:00",
        "completed_at": "2026-10-02T09:01:00+00:00",
        "database": {
            "tables": source["tables"],
            "rows": source["rows"],
            "all_fingerprints_match": True,
            "archive_sha256": source["database_sha256"],
        },
        "objects": {
            "count": source["objects"],
            "bytes": source["object_bytes"],
            "all_readback_bytes_match": True,
            "inventory_exact": True,
        },
        "original_snapshot_read_during_restore": False,
        "original_containers_still_running": True,
        "plaintext_host_archive_written": False,
        "cleanup": [{"id": "a" * 64, "removed": True}, {"id": "b" * 64, "removed": True}],
    }


def attempt(source, result):
    from scripts.server_backup import restore_catalog as catalog

    raw = encode(result)
    verifier = b"synthetic trusted restore runner, never used as real evidence"
    return catalog.record_restore(
        source=source,
        report_bytes=raw,
        expected_report_sha256=sha(raw),
        verifier_bytes=verifier,
        expected_verifier_sha256=sha(verifier),
    )


def inputs(source, attempts, **kwargs):
    from scripts.server_backup import restore_catalog as catalog

    document = catalog.build_catalog(
        snapshots=[{"source": source, "attempts": attempts}],
        references=kwargs.get("references", {}),
        pinned_ids=kwargs.get("pinned_ids", set()),
        in_progress_ids=kwargs.get("in_progress_ids", set()),
    )
    raw = encode(document)
    return catalog.retention_inputs(raw, expected_sha256=sha(raw))


def test_only_bound_complete_restore_receipt_creates_proof_and_failure_stays_protected(tmp_path):
    source = inspect(tmp_path)
    success = attempt(source, report(source))
    proofs, protected = inputs(source, [success])
    assert protected == set()
    assert proofs[source["snapshot_id"]]["content_sha256"] == source["content_sha256"]
    assert proofs[source["snapshot_id"]]["evidence_sha256"] == success["evidence_sha256"]
    failed = report(source)
    failed.update(
        status="original_container_state_changed", original_containers_still_running=False
    )
    failure = attempt(source, failed)
    proofs, protected = inputs(source, [success, failure])
    assert protected == {source["snapshot_id"]}
    assert failure["report"]["status"] == "original_container_state_changed"
    assert len(proofs) == 1  # old success does not erase the later failed attempt


def test_content_identity_ignores_capture_time_and_dump_encoding_but_covers_business_data(tmp_path):
    first = inspect(tmp_path)
    equivalent = inspect(tmp_path, stamp="2026-10-02T08:10:00+00:00", dump=b"PGDMP-other-encoding")
    assert first["ciphertext_sha256"] != equivalent["ciphertext_sha256"]
    assert first["database_sha256"] != equivalent["database_sha256"]
    assert first["content_sha256"] == equivalent["content_sha256"]
    for change in ({"table": b"changed rows"}, {"blob": b"changed object"}, {"release": "rc.17"}):
        assert inspect(tmp_path, **change)["content_sha256"] != first["content_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"source_snapshot": "snapshot-wrong"},
        {"ciphertext_sha256": "f" * 64},
        {"database": {}},
        {"objects": {}},
        {"original_snapshot_read_during_restore": True},
        {"cleanup": [{"id": "a" * 64, "removed": False}]},
        {"completed_at": "2026-10-01T00:00:00+00:00"},
        {"error_type": "UnknownError"},
    ],
)
def test_incomplete_or_misbound_success_is_rejected(tmp_path, change):
    from scripts.server_backup import restore_catalog as catalog

    source = inspect(tmp_path)
    with pytest.raises(catalog.CatalogError):
        attempt(source, {**report(source), **change})


def test_evidence_ciphertext_and_catalog_tampering_and_duplicate_keys_are_rejected(tmp_path):
    from scripts.server_backup import restore_catalog as catalog

    raw, identity, _process = bundle(tmp_path)
    with patch("subprocess.run", side_effect=AssertionError("must reject before decryption")):
        with pytest.raises(catalog.CatalogError):
            catalog.inspect_snapshot(
                snapshot_id="snapshot-test-1",
                ciphertext=raw + b"changed",
                expected_sha256=sha(raw),
                age="synthetic-age",
                identity=identity,
            )
    source = inspect(tmp_path)
    original = encode(report(source))
    with pytest.raises(catalog.CatalogError):
        catalog.record_restore(
            source=source,
            report_bytes=original + b" ",
            expected_report_sha256=sha(original),
            verifier_bytes=b"runner",
            expected_verifier_sha256=sha(b"runner"),
        )
    with pytest.raises(catalog.CatalogError):
        catalog.record_restore(
            source=source,
            report_bytes=original,
            expected_report_sha256=sha(original),
            verifier_bytes=b"changed runner",
            expected_verifier_sha256=sha(b"runner"),
        )
    document = catalog.build_catalog(
        snapshots=[], references={}, pinned_ids=set(), in_progress_ids=set()
    )
    serialized = encode(document)
    with pytest.raises(catalog.CatalogError):
        catalog.retention_inputs(serialized + b" ", expected_sha256=sha(serialized))
    duplicate = b'{"schema_version":1,"schema_version":1}'
    with pytest.raises(catalog.CatalogError):
        catalog.retention_inputs(duplicate, expected_sha256=sha(duplicate))


@pytest.mark.parametrize("field", ["references", "pinned_ids", "in_progress_ids"])
def test_explicit_owners_and_unknown_or_interrupted_work_remain_protected(tmp_path, field):
    source = inspect(tmp_path)
    ids = {source["snapshot_id"], "snapshot-not-in-inventory"}
    value = {"recovery-drill": sorted(ids)} if field == "references" else ids
    proofs, protected = inputs(source, [attempt(source, report(source))], **{field: value})
    assert len(proofs) == 1
    assert protected == ids


def test_catalog_reaches_retention_planner_without_network_mutations(tmp_path):
    from scripts.server_backup import restore_catalog as catalog
    from scripts.server_backup.server_publication import marker_bytes, snapshot_record
    from tests.deployment.test_server_backup_retention import BUCKET, PREFIX, Storage

    store, snapshots = Storage(), []
    for n in range(1, 8):
        # Six different logical contents; snapshot 7 is another encryption of 6.
        raw, identity, process = bundle(
            tmp_path, table=str(min(n, 6)).encode(), stamp=f"2026-10-02T08:0{n}:00+00:00"
        )
        sid = f"snapshot-test-{n}"
        with patch("subprocess.run", side_effect=process):
            source = catalog.inspect_snapshot(
                snapshot_id=sid,
                ciphertext=raw,
                expected_sha256=sha(raw),
                age="synthetic-age",
                identity=identity,
            )
        record = snapshot_record(
            bucket=BUCKET,
            prefix=PREFIX,
            snapshot_id=sid,
            ciphertext=raw,
            captured_at=source["captured_at"],
        )
        store.objects[record["ciphertext_key"]] = raw
        store.objects[PREFIX + sid + ".complete.json"] = marker_bytes(record)
        snapshots.append({"source": source, "attempts": [attempt(source, report(source))]})
    document = catalog.build_catalog(
        snapshots=snapshots,
        references={"ongoing-drill": ["snapshot-test-1"]},
        pinned_ids=set(),
        in_progress_ids=set(),
    )
    raw = encode(document)
    before = dict(store.objects)
    result = catalog.plan_from_catalog(
        catalog_bytes=raw,
        expected_catalog_sha256=sha(raw),
        client=store,
        bucket=BUCKET,
        prefix=PREFIX,
        at="2026-10-02T10:00:00+00:00",
    )
    assert [row["snapshot_id"] for row in result["candidates"]] == ["snapshot-test-6"]
    assert len(result["retained"]) == 6  # five contents plus the referenced oldest
    assert result["approval_granted"] is False
    assert store.objects == before and store.puts == []
