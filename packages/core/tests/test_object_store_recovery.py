from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO
from uuid import UUID

import pytest

from enterprise_doc_core.recovery.object_store import (
    ObjectReference,
    ObjectStoreRecoveryError,
    create_snapshot,
    parse_snapshot_manifest,
    restore_snapshot,
)


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = dict(objects)
        self.copy_calls: list[dict[str, object]] = []
        self.put_calls: list[dict[str, object]] = []
        self.fail_copy_call: int | None = None
        self.multipart_uploads: dict[str, dict[str, object]] = {}
        self.multipart_create_calls: list[dict[str, object]] = []
        self.multipart_complete_calls: list[dict[str, object]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        body = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(body),
            "ETag": f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"',
            "Metadata": {},
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def copy_object(self, **kwargs: object) -> dict[str, object]:
        self.copy_calls.append(kwargs)
        if self.fail_copy_call == len(self.copy_calls):
            raise RuntimeError("super-secret copy failure")
        source = kwargs["CopySource"]
        assert isinstance(source, dict)
        body = self.objects[(str(source["Bucket"]), str(source["Key"]))]
        destination = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        self.objects[destination] = body
        head = self.head_object(Bucket=destination[0], Key=destination[1])
        return {"CopyObjectResult": {"ETag": head["ETag"]}}

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.put_calls.append(kwargs)
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        destination = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        self.objects[destination] = body
        return {"ETag": self.head_object(Bucket=destination[0], Key=destination[1])["ETag"]}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        bucket = str(kwargs["Bucket"])
        prefix = str(kwargs["Prefix"])
        contents = [
            {"Key": key, "Size": len(body)}
            for (object_bucket, key), body in sorted(self.objects.items())
            if object_bucket == bucket and key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def create_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        self.multipart_create_calls.append(kwargs)
        upload_id = f"upload-{len(self.multipart_create_calls)}"
        self.multipart_uploads[upload_id] = {"request": kwargs, "parts": {}}
        return {"UploadId": upload_id}

    def upload_part_copy(self, **kwargs: object) -> dict[str, object]:
        upload = self.multipart_uploads[str(kwargs["UploadId"])]
        source = kwargs["CopySource"]
        assert isinstance(source, dict)
        body = self.objects[(str(source["Bucket"]), str(source["Key"]))]
        byte_range = str(kwargs["CopySourceRange"]).removeprefix("bytes=")
        start, end = (int(value) for value in byte_range.split("-", maxsplit=1))
        part = body[start : end + 1]
        parts = upload["parts"]
        assert isinstance(parts, dict)
        part_number = int(kwargs["PartNumber"])
        parts[part_number] = part
        return {"CopyPartResult": {"ETag": f'"part-{part_number}"'}}

    def complete_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        self.multipart_complete_calls.append(kwargs)
        upload = self.multipart_uploads.pop(str(kwargs["UploadId"]))
        request = upload["request"]
        parts = upload["parts"]
        assert isinstance(request, dict)
        assert isinstance(parts, dict)
        body = b"".join(parts[number] for number in sorted(parts))
        self.objects[(str(request["Bucket"]), str(request["Key"]))] = body
        return {"ETag": '"multipart"'}

    def abort_multipart_upload(self, **kwargs: object) -> None:
        self.multipart_uploads.pop(str(kwargs["UploadId"]), None)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _source_fixture() -> tuple[FakeS3Client, tuple[ObjectReference, ...]]:
    document = b"reviewed contract"
    artifact = b'{"answer":"evidence"}'
    client = FakeS3Client(
        {
            ("documents", "tenant/document.txt"): document,
            ("artifacts", "tenant/answer.json"): artifact,
        }
    )
    references = (
        ObjectReference(
            reference_type="document_version",
            reference_id=UUID("00000000-0000-0000-0000-000000000001"),
            bucket="documents",
            key="tenant/document.txt",
            size_bytes=len(document),
            sha256=_sha256(document),
        ),
        ObjectReference(
            reference_type="agent_artifact",
            reference_id=UUID("00000000-0000-0000-0000-000000000002"),
            bucket="artifacts",
            key="tenant/answer.json",
            size_bytes=len(artifact),
            sha256=_sha256(artifact),
        ),
    )
    return client, references


def _snapshot(
    client: FakeS3Client,
    references: tuple[ObjectReference, ...],
    *,
    confirm: bool,
):
    return create_snapshot(
        client=client,
        endpoint_host="account.r2.cloudflarestorage.com",
        expected_endpoint_host="account.r2.cloudflarestorage.com",
        allowed_buckets=frozenset({"documents", "artifacts"}),
        manifest_bucket="documents",
        drill_id="20260805-staging",
        references=references,
        confirm=confirm,
    )


def _restore(
    client: FakeS3Client,
    references: tuple[ObjectReference, ...],
    manifest: object,
    *,
    confirm: bool,
):
    return restore_snapshot(
        client=client,
        manifest=manifest,
        expected_manifest_sha256=manifest.manifest_sha256,
        endpoint_host="account.r2.cloudflarestorage.com",
        expected_endpoint_host="account.r2.cloudflarestorage.com",
        allowed_buckets=frozenset({"documents", "artifacts"}),
        database_name="enterprise_doc_restore_20260805t094423z",
        expected_database_name="enterprise_doc_restore_20260805t094423z",
        restore_id="20260805-staging",
        references=references,
        confirm=confirm,
    )


def test_snapshot_and_restore_are_read_only_until_confirmed() -> None:
    client, references = _source_fixture()

    planned = _snapshot(client, references, confirm=False)

    assert planned.status == "planned"
    assert planned.object_count == 2
    assert client.copy_calls == []
    assert client.put_calls == []

    completed = _snapshot(client, references, confirm=True)

    assert completed.status == "passed"
    assert completed.object_count == 2
    assert len(client.copy_calls) == 2
    assert len(client.put_calls) == 1
    assert client.put_calls[0]["IfNoneMatch"] == "*"
    assert completed.manifest.manifest_sha256

    restore_plan = _restore(client, references, completed.manifest, confirm=False)

    assert restore_plan.status == "planned"
    assert restore_plan.object_count == 2
    assert len(client.copy_calls) == 2

    restored = _restore(client, references, completed.manifest, confirm=True)

    assert restored.status == "passed"
    assert restored.copied_count == 2
    assert restored.existing_count == 0
    assert len(client.copy_calls) == 4

    rerun = _snapshot(client, references, confirm=True)
    restore_rerun = _restore(client, references, completed.manifest, confirm=True)

    assert rerun.status == "passed"
    assert rerun.manifest == completed.manifest
    assert rerun.copied_count == 0
    assert rerun.existing_count == 2
    assert len(client.copy_calls) == 4
    assert len(client.put_calls) == 1
    assert restore_rerun.status == "passed"
    assert restore_rerun.copied_count == 0
    assert restore_rerun.existing_count == 2


def test_snapshot_rejects_source_checksum_mismatch_and_missing_source() -> None:
    client, references = _source_fixture()
    bad_reference = replace(references[0], sha256="0" * 64)

    with pytest.raises(ObjectStoreRecoveryError, match="SHA-256"):
        _snapshot(client, (bad_reference, references[1]), confirm=False)

    del client.objects[(references[0].bucket, references[0].key)]
    with pytest.raises(ObjectStoreRecoveryError, match="head failed"):
        _snapshot(client, references, confirm=False)


def test_snapshot_rejects_conflicting_existing_target() -> None:
    client, references = _source_fixture()
    plan = _snapshot(client, references, confirm=False)
    first = plan.manifest.objects[0]
    client.objects[(first.bucket, first.snapshot_key)] = b"wrong snapshot"

    with pytest.raises(ObjectStoreRecoveryError, match="size"):
        _snapshot(client, references, confirm=True)

    assert client.put_calls == []


def test_snapshot_resumes_after_a_partial_copy_without_overwriting_verified_objects() -> None:
    client, references = _source_fixture()
    client.fail_copy_call = 2

    with pytest.raises(ObjectStoreRecoveryError, match="snapshot copy failed"):
        _snapshot(client, references, confirm=True)

    assert len(client.copy_calls) == 2
    assert client.put_calls == []
    client.fail_copy_call = None

    completed = _snapshot(client, references, confirm=True)

    assert completed.status == "passed"
    assert completed.copied_count == 1
    assert completed.existing_count == 1
    assert len(client.copy_calls) == 3


def test_manifest_digest_and_database_reference_set_are_fail_closed() -> None:
    client, references = _source_fixture()
    completed = _snapshot(client, references, confirm=True)
    raw = json.loads(completed.manifest.render())
    raw["objects"][0]["size_bytes"] += 1
    tampered = json.dumps(raw).encode()

    with pytest.raises(ObjectStoreRecoveryError, match="digest"):
        parse_snapshot_manifest(tampered)

    with pytest.raises(ObjectStoreRecoveryError, match="database references"):
        _restore(client, references[1:], completed.manifest, confirm=False)

    bad_digest_manifest = replace(completed.manifest, manifest_sha256="0" * 64)
    with pytest.raises(ObjectStoreRecoveryError, match="digest"):
        _restore(client, references, bad_digest_manifest, confirm=False)


def test_restore_requires_expected_endpoint_bucket_and_isolated_database() -> None:
    client, references = _source_fixture()
    completed = _snapshot(client, references, confirm=True)

    with pytest.raises(ObjectStoreRecoveryError, match="endpoint host"):
        restore_snapshot(
            client=client,
            manifest=completed.manifest,
            expected_manifest_sha256=completed.manifest.manifest_sha256,
            endpoint_host="account.r2.cloudflarestorage.com",
            expected_endpoint_host="other.r2.cloudflarestorage.com",
            allowed_buckets=frozenset({"documents", "artifacts"}),
            database_name="enterprise_doc_restore_20260805t094423z",
            expected_database_name="enterprise_doc_restore_20260805t094423z",
            restore_id="20260805-staging",
            references=references,
            confirm=False,
        )

    with pytest.raises(ObjectStoreRecoveryError, match="bucket"):
        restore_snapshot(
            client=client,
            manifest=completed.manifest,
            expected_manifest_sha256=completed.manifest.manifest_sha256,
            endpoint_host="account.r2.cloudflarestorage.com",
            expected_endpoint_host="account.r2.cloudflarestorage.com",
            allowed_buckets=frozenset({"documents"}),
            database_name="enterprise_doc_restore_20260805t094423z",
            expected_database_name="enterprise_doc_restore_20260805t094423z",
            restore_id="20260805-staging",
            references=references,
            confirm=False,
        )

    with pytest.raises(ObjectStoreRecoveryError, match="isolated restore database"):
        restore_snapshot(
            client=client,
            manifest=completed.manifest,
            expected_manifest_sha256=completed.manifest.manifest_sha256,
            endpoint_host="account.r2.cloudflarestorage.com",
            expected_endpoint_host="account.r2.cloudflarestorage.com",
            allowed_buckets=frozenset({"documents", "artifacts"}),
            database_name="postgres",
            expected_database_name="postgres",
            restore_id="20260805-staging",
            references=references,
            confirm=False,
        )


def test_restore_rejects_unexpected_prefix_objects_before_copying() -> None:
    client, references = _source_fixture()
    completed = _snapshot(client, references, confirm=True)
    client.objects[
        (
            "documents",
            "enterprise-doc-recovery/restores/20260805-staging/unexpected",
        )
    ] = b"unexpected"
    copy_count = len(client.copy_calls)

    with pytest.raises(ObjectStoreRecoveryError, match="unexpected objects"):
        _restore(client, references, completed.manifest, confirm=True)

    assert len(client.copy_calls) == copy_count


def test_restore_rejects_conflicting_target_and_resumes_after_partial_copy() -> None:
    conflict_client, conflict_references = _source_fixture()
    conflict_snapshot = _snapshot(conflict_client, conflict_references, confirm=True)
    plan = _restore(
        conflict_client,
        conflict_references,
        conflict_snapshot.manifest,
        confirm=False,
    )
    first = conflict_snapshot.manifest.objects[0]
    first_restore_key = f"{plan.restore_prefix}/objects/{first.snapshot_key.rsplit('/', 1)[-1]}"
    conflict_client.objects[(first.bucket, first_restore_key)] = b"wrong restore"
    copy_count = len(conflict_client.copy_calls)

    with pytest.raises(ObjectStoreRecoveryError, match="size"):
        _restore(
            conflict_client,
            conflict_references,
            conflict_snapshot.manifest,
            confirm=True,
        )
    assert len(conflict_client.copy_calls) == copy_count

    client, references = _source_fixture()
    completed = _snapshot(client, references, confirm=True)
    client.fail_copy_call = 4
    with pytest.raises(ObjectStoreRecoveryError, match="restore copy failed"):
        _restore(client, references, completed.manifest, confirm=True)

    client.fail_copy_call = None
    resumed = _restore(client, references, completed.manifest, confirm=True)

    assert resumed.status == "passed"
    assert resumed.copied_count == 1
    assert resumed.existing_count == 1


def test_public_records_do_not_contain_database_object_keys_or_reference_ids() -> None:
    client, references = _source_fixture()
    snapshot = _snapshot(client, references, confirm=True)
    restored = _restore(client, references, snapshot.manifest, confirm=True)

    rendered_snapshot = json.dumps(snapshot.to_record())
    rendered_restore = json.dumps(restored.to_record())
    for sensitive_value in (
        "tenant/document.txt",
        "tenant/answer.json",
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ):
        assert sensitive_value not in rendered_snapshot
        assert sensitive_value not in rendered_restore


def test_snapshot_uses_multipart_copy_above_the_single_copy_limit() -> None:
    client, references = _source_fixture()

    completed = create_snapshot(
        client=client,
        endpoint_host="account.r2.cloudflarestorage.com",
        expected_endpoint_host="account.r2.cloudflarestorage.com",
        allowed_buckets=frozenset({"documents", "artifacts"}),
        manifest_bucket="documents",
        drill_id="20260805-multipart",
        references=references,
        confirm=True,
        single_copy_max_bytes=10,
        multipart_copy_part_size=8,
    )

    assert completed.status == "passed"
    assert client.copy_calls == []
    assert len(client.multipart_create_calls) == 2
    assert len(client.multipart_complete_calls) == 2
