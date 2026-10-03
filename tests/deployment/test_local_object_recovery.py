from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.local_object_recovery import (
    LocalObjectRecoveryError,
    capture_objects,
    restore_objects,
)

from enterprise_doc_core.recovery.object_store import ObjectReference


class ObjectStore:
    def __init__(self, endpoint: str, objects: dict[tuple[str, str], bytes]) -> None:
        self.meta = SimpleNamespace(endpoint_url=endpoint)
        self.objects = dict(objects)
        self.writes: list[tuple[str, str]] = []
        self.reads = 0

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.reads += 1
        value = self.objects[Bucket, Key]
        return {"Body": io.BytesIO(value), "ContentLength": len(value)}

    def list_objects_v2(self, *, Bucket: str, MaxKeys: int) -> dict[str, object]:
        return {"Contents": [{"Key": k} for b, k in self.objects if b == Bucket][:MaxKeys]}

    def put_object(self, *, Bucket: str, Key: str, Body: object, IfNoneMatch: str) -> None:
        assert IfNoneMatch == "*"
        if (Bucket, Key) in self.objects:
            raise RuntimeError("private-key-must-not-appear")
        assert hasattr(Body, "read")
        self.objects[Bucket, Key] = Body.read()
        self.writes.append((Bucket, Key))


def reference(body: bytes = b"document") -> ObjectReference:
    return ObjectReference(
        "document_version",
        uuid4(),
        "documents",
        "private/source.txt",
        len(body),
        hashlib.sha256(body).hexdigest(),
    )


def capture(
    path: Path, refs: tuple[ObjectReference, ...], body: bytes = b"document"
) -> tuple[dict, ObjectStore]:
    source = ObjectStore("https://source.example", {("documents", "private/source.txt"): body})
    result = capture_objects(
        client=source,
        references=refs,
        output_dir=path,
        allowed_buckets=frozenset({"documents"}),
        max_objects=10,
        max_total_bytes=100,
    )
    return result, source


def test_round_trip_binds_references_and_never_writes_source(tmp_path: Path) -> None:
    ref = reference()
    result, source = capture(tmp_path / "snapshot", (ref,))
    manifest = tmp_path / "snapshot" / "objects.json"
    payload = json.loads(manifest.read_text())
    assert payload["objects"][0]["references"] == [
        {"reference_type": "document_version", "reference_id": str(ref.reference_id)}
    ]
    destination = ObjectStore("http://127.0.0.1:19000", {})
    restored = restore_objects(
        client=destination,
        manifest_path=manifest,
        expected_sha256=result["manifest_sha256"],
        allowed_buckets=frozenset({"documents"}),
    )
    assert source.writes == []
    assert restored["verified_objects"] == 1
    assert destination.objects == source.objects
    assert destination.reads == 1


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    marker = tmp_path / "old"
    marker.write_text("original")
    with pytest.raises(LocalObjectRecoveryError, match="already exists"):
        capture(tmp_path, (reference(),))
    assert marker.read_text() == "original"


@pytest.mark.parametrize("body", [b"trunc", b"documEnt", b"document-extra"])
def test_failed_download_never_publishes_manifest(tmp_path: Path, body: bytes) -> None:
    with pytest.raises(LocalObjectRecoveryError):
        capture(tmp_path / "bad", (reference(),), body)
    assert not (tmp_path / "bad" / "objects.json").exists()


def test_bad_reference_or_budget_rejected_before_source_read(tmp_path: Path) -> None:
    source = ObjectStore("https://source.example", {})
    for ref in (
        reference(b"a" * 101),
        ObjectReference("document_version", uuid4(), "other", "x", 1, "a" * 64),
    ):
        with pytest.raises(LocalObjectRecoveryError):
            capture_objects(
                client=source,
                references=(ref,),
                output_dir=tmp_path / "bad",
                allowed_buckets=frozenset({"documents"}),
                max_objects=10,
                max_total_bytes=100,
            )
    assert source.reads == 0
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("mutation", ["manifest", "file", "missing"])
def test_tampering_rejected_before_any_write(tmp_path: Path, mutation: str) -> None:
    result, _ = capture(tmp_path / "snapshot", (reference(),))
    manifest = tmp_path / "snapshot" / "objects.json"
    blob = next((tmp_path / "snapshot").glob("*.blob"))
    if mutation == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif mutation == "file":
        blob.write_bytes(b"modified")
    else:
        blob.unlink()
    target = ObjectStore("http://127.0.0.1:19000", {})
    with pytest.raises(LocalObjectRecoveryError):
        restore_objects(
            client=target,
            manifest_path=manifest,
            expected_sha256=result["manifest_sha256"],
            allowed_buckets=frozenset({"documents"}),
        )
    assert target.writes == []


@pytest.mark.parametrize(
    "endpoint,existing",
    [("https://source.example", {}), ("http://127.0.0.1:19000", {("documents", "old"): b"old"})],
)
def test_nonlocal_or_nonempty_target_refused(tmp_path: Path, endpoint: str, existing: dict) -> None:
    result, _ = capture(tmp_path / "snapshot", (reference(),))
    target = ObjectStore(endpoint, existing)
    with pytest.raises(LocalObjectRecoveryError):
        restore_objects(
            client=target,
            manifest_path=tmp_path / "snapshot" / "objects.json",
            expected_sha256=result["manifest_sha256"],
            allowed_buckets=frozenset({"documents"}),
        )
    assert target.objects == existing
    assert target.writes == []


def test_duplicate_or_conflicting_references_rejected(tmp_path: Path) -> None:
    first = reference()
    conflict = ObjectReference("document_version", uuid4(), first.bucket, first.key, 8, "a" * 64)
    for refs in ((first, first), (first, conflict)):
        with pytest.raises(LocalObjectRecoveryError):
            capture(tmp_path / "bad", refs)
    assert not (tmp_path / "bad").exists()


def test_multiple_references_share_one_verified_download(tmp_path: Path) -> None:
    result, source = capture(tmp_path / "snapshot", (reference(), reference()))
    assert result["object_count"] == source.reads == 1
    assert result["reference_count"] == 2


def test_object_count_budget_is_enforced_before_io(tmp_path: Path) -> None:
    second = ObjectReference("agent_artifact", uuid4(), "documents", "second", 0, "a" * 64)
    source = ObjectStore("https://source.example", {})
    with pytest.raises(LocalObjectRecoveryError, match="budget"):
        capture_objects(
            client=source,
            references=(reference(), second),
            output_dir=tmp_path / "bad",
            allowed_buckets=frozenset({"documents"}),
            max_objects=1,
            max_total_bytes=100,
        )
    assert source.reads == 0


def test_transport_errors_are_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret credential and private/source.txt")

    monkeypatch.setattr(ObjectStore, "get_object", fail)
    with pytest.raises(LocalObjectRecoveryError) as failure:
        capture(tmp_path / "snapshot", (reference(),))
    assert "secret" not in str(failure.value)
    assert "private/source" not in str(failure.value)
    assert failure.value.__suppress_context__


def test_restore_readback_corruption_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = capture(tmp_path / "snapshot", (reference(),))
    target = ObjectStore("http://127.0.0.1:19000", {})
    monkeypatch.setattr(
        target,
        "get_object",
        lambda **kwargs: {
            "Body": io.BytesIO(b"documEnt"),
            "ContentLength": 8,
        },
    )
    with pytest.raises(LocalObjectRecoveryError, match="bytes"):
        restore_objects(
            client=target,
            manifest_path=tmp_path / "snapshot" / "objects.json",
            expected_sha256=result["manifest_sha256"],
            allowed_buckets=frozenset({"documents"}),
        )


def test_all_files_verified_before_restore_writes(tmp_path: Path) -> None:
    first = reference()
    second = ObjectReference("agent_artifact", uuid4(), "documents", "second", 8, first.sha256)
    source = ObjectStore(
        "https://source.example",
        {
            ("documents", "private/source.txt"): b"document",
            ("documents", "second"): b"document",
        },
    )
    result = capture_objects(
        client=source,
        references=(first, second),
        output_dir=tmp_path / "snapshot",
        allowed_buckets=frozenset({"documents"}),
        max_objects=2,
        max_total_bytes=100,
    )
    manifest = tmp_path / "snapshot" / "objects.json"
    second_file = json.loads(manifest.read_bytes())["objects"][1]["file"]
    (manifest.parent / second_file).write_bytes(b"corrupt")
    target = ObjectStore("http://127.0.0.1:19000", {})
    with pytest.raises(LocalObjectRecoveryError):
        restore_objects(
            client=target,
            manifest_path=manifest,
            expected_sha256=result["manifest_sha256"],
            allowed_buckets=frozenset({"documents"}),
        )
    assert target.writes == []
