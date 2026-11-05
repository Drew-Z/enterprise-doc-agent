"""Durable multipart transport through real SQLite and a faultable S3 boundary."""

import hashlib
import io
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from scripts.server_backup.backup_runtime import connect
from scripts.server_backup.multipart_publication import upload_ciphertext
from scripts.server_backup.remote_retention import publish_budgeted_snapshot
from scripts.server_backup.server_publication import PublicationError, snapshot_record


def error(status, operation):
    return ClientError(
        {"Error": {"Code": str(status)}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class Store:
    def __init__(self):
        self.meta = SimpleNamespace(
            endpoint_url="https://storage.example.test",
            service_model=SimpleNamespace(
                operation_model=lambda name: SimpleNamespace(
                    input_shape=SimpleNamespace(members={"IfNoneMatch": True})
                )
            ),
        )
        self.objects = {}
        self.uploads = {}
        self.creates = 0
        self.complete_calls = 0
        self.fail_part = False
        self.lose_create = False
        self.lose_complete = False
        self.corrupt = False
        self.barrier = None
        self.lock = threading.Lock()

    def list_objects_v2(self, **kw):
        return {
            "Contents": [
                {"Key": k, "Size": len(v), "ETag": '"object"'} for k, v in self.objects.items()
            ]
        }

    def list_multipart_uploads(self, **kw):
        return {"Uploads": [{"Key": v["key"], "UploadId": k} for k, v in self.uploads.items()]}

    def create_multipart_upload(self, **kw):
        self.creates += 1
        upload = "upload-" + str(self.creates)
        self.uploads[upload] = {"key": kw["Key"], "parts": {}}
        if self.lose_create:
            self.lose_create = False
            raise TimeoutError("lost create response")
        return {"UploadId": upload}

    def list_parts(self, **kw):
        return {
            "Parts": [
                {"PartNumber": n, "Size": len(v), "ETag": self.etag(v)}
                for n, v in sorted(self.uploads[kw["UploadId"]]["parts"].items())
            ]
        }

    @staticmethod
    def etag(raw):
        return '"' + hashlib.md5(raw, usedforsecurity=False).hexdigest() + '"'

    def upload_part(self, **kw):
        if self.barrier:
            self.barrier.wait(timeout=5)
        with self.lock:
            self.uploads[kw["UploadId"]]["parts"][kw["PartNumber"]] = kw["Body"]
        if self.fail_part and kw["PartNumber"] == 1:
            self.fail_part = False
            raise TimeoutError("lost part response")
        return {"ETag": self.etag(kw["Body"])}

    def complete_multipart_upload(self, **kw):
        assert kw["IfNoneMatch"] == "*"
        self.complete_calls += 1
        if kw["Key"] in self.objects:
            raise error(412, "CompleteMultipartUpload")
        upload = self.uploads.pop(kw["UploadId"])
        self.objects[kw["Key"]] = b"".join(
            upload["parts"][p["PartNumber"]] for p in kw["MultipartUpload"]["Parts"]
        )
        if self.lose_complete:
            self.lose_complete = False
            raise TimeoutError("lost completion response")
        return {}

    def get_object(self, **kw):
        if kw["Key"] not in self.objects:
            raise error(404, "GetObject")
        raw = self.objects[kw["Key"]]
        if self.corrupt:
            raw = raw[:-1] + bytes([raw[-1] ^ 1])
        return {"ContentLength": len(raw), "Body": io.BytesIO(raw)}

    def abort_multipart_upload(self, **kw):
        self.uploads.pop(kw["UploadId"], None)
        return {}

    def put_object(self, **kw):
        assert kw["IfNoneMatch"] == "*"
        if kw["Key"] in self.objects:
            raise error(412, "PutObject")
        self.objects[kw["Key"]] = kw["Body"]
        return {}


@pytest.fixture
def case(tmp_path):
    raw = b"age-encryption.org/v1\n" + b"x" * (9 * 1024 * 1024)
    record = snapshot_record(
        bucket="recovery-test",
        prefix="operations-recovery/v1/",
        snapshot_id="snapshot-20261004-test",
        ciphertext=raw,
        captured_at="2026-10-04T10:00:00+00:00",
    )
    with connect(tmp_path) as db:
        db.execute(
            "INSERT INTO attempts(id,stage,created_at,captured_at,ciphertext_sha256,"
            "ciphertext_bytes) VALUES(?,'sealed',?,?,?,?)",
            (
                record["snapshot_id"],
                record["captured_at"],
                record["captured_at"],
                record["ciphertext_sha256"],
                len(raw),
            ),
        )
    return tmp_path / "runtime.sqlite", record, raw


def invoke(store, case, **kw):
    path, record, raw = case
    return upload_ciphertext(
        client=store, bucket="recovery-test", record=record, ciphertext=raw, journal_path=path, **kw
    )


def test_parallel_upload_complete_readback_and_restart_replay(case):
    store = Store()
    store.barrier = threading.Barrier(2)
    invoke(store, case)
    store.barrier = None
    invoke(store, case)
    assert store.creates == store.complete_calls == 1
    assert store.objects == {case[1]["ciphertext_key"]: case[2]}
    assert not store.uploads


@pytest.mark.parametrize("fault", ["lose_create", "fail_part", "lose_complete"])
def test_uncertain_network_result_resumes_original_transfer(case, fault):
    store = Store()
    setattr(store, fault, True)
    with pytest.raises(PublicationError):
        invoke(store, case)
    invoke(store, case)
    assert store.creates == 1
    assert store.objects[case[1]["ciphertext_key"]] == case[2]
    assert not store.uploads


def test_existing_different_ciphertext_is_never_overwritten(case):
    store = Store()
    key = case[1]["ciphertext_key"]
    store.objects[key] = b"different"
    with pytest.raises(PublicationError):
        invoke(store, case)
    assert store.objects[key] == b"different" and store.creates == 0


def test_corrupt_full_readback_cannot_verify(case):
    store = Store()
    store.corrupt = True
    with pytest.raises(PublicationError):
        invoke(store, case)
    with sqlite3.connect(case[0]) as db:
        assert db.execute("SELECT phase FROM multipart_transfers").fetchone()[0] != "verified"


def test_capacity_rejection_happens_before_creating_upload(case):
    store = Store()
    with pytest.raises(PublicationError):
        invoke(store, case, max_bytes=1)
    assert store.creates == 0 and not store.objects


def test_changed_snapshot_identity_rejected_before_network(case):
    store = Store()
    bad = (case[0], {**case[1], "captured_at": "2026-10-04T11:00:00+00:00"}, case[2])
    with pytest.raises(PublicationError):
        invoke(store, bad)
    assert store.creates == 0


def test_public_publisher_marks_complete_only_after_verified_ciphertext(case):
    store = Store()
    path, record, raw = case
    arguments = dict(
        client=store,
        bucket="recovery-test",
        prefix="operations-recovery/v1/",
        snapshot_id=record["snapshot_id"],
        ciphertext=raw,
        captured_at=record["captured_at"],
        multipart_journal=path,
    )
    store.fail_part = True
    with pytest.raises(PublicationError):
        publish_budgeted_snapshot(**arguments)
    assert not store.objects
    result = publish_budgeted_snapshot(**arguments)
    assert result == record
    assert len(store.objects) == 2 and store.creates == 1
    assert publish_budgeted_snapshot(**arguments) == record
    assert store.complete_calls == 1


def test_unknown_other_upload_reserves_full_ciphertext_budget(case):
    store = Store()
    store.uploads["orphan"] = {"key": "operations-recovery/v1/snapshot-other.tar.age", "parts": {}}
    with pytest.raises(PublicationError):
        invoke(store, case, max_bytes=64 * 1024 * 1024)
    assert store.creates == 0 and "orphan" in store.uploads


def test_unresolved_create_is_not_repeated_when_listing_is_empty(case):
    store = Store()
    store.lose_create = True
    with pytest.raises(PublicationError):
        invoke(store, case)
    store.uploads.clear()
    for _ in range(2):
        with pytest.raises(PublicationError):
            invoke(store, case)
    assert store.creates == 1 and not store.objects


def test_runtime_restart_reuses_sealed_source_without_recapture(tmp_path):
    from scripts.server_backup.backup_runtime import now, run_tick

    store = Store()
    store.lose_complete = True
    captures = []
    source_time = now()
    raw = b"age-encryption.org/v1\n" + b"x" * (9 * 1024 * 1024)

    def capture():
        captures.append(source_time)
        return {"captured_at": source_time}

    def publish(**kwargs):
        return publish_budgeted_snapshot(
            client=store,
            bucket="recovery-test",
            prefix="operations-recovery/v1/",
            multipart_journal=tmp_path / "runtime.sqlite",
            **kwargs,
        )

    args = dict(
        root=tmp_path,
        config_fingerprint="same-config",
        capture=capture,
        seal=lambda _: raw,
        publish=publish,
    )
    first = run_tick(**args)
    assert first["status"] == "upload_pending"
    assert len(store.objects) == 1
    second = run_tick(**args)
    assert second["status"] == "succeeded"
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["captured_at"] == source_time
    assert captures == [source_time] and store.creates == 1
    assert len(store.objects) == 2
    assert not list((tmp_path / "spool").iterdir())


def test_endpoint_change_cannot_adopt_pending_upload(case):
    store = Store()
    store.lose_create = True
    with pytest.raises(PublicationError):
        invoke(store, case)
    store.meta.endpoint_url = "https://other.example.test"
    with pytest.raises(PublicationError):
        invoke(store, case)
    assert store.creates == 1 and not store.objects


def test_persisted_upload_identity_resumes_even_if_listing_omits_it(case):
    store = Store()
    store.fail_part = True
    with pytest.raises(PublicationError):
        invoke(store, case)
    store.list_multipart_uploads = lambda **kw: {"Uploads": []}
    invoke(store, case)
    assert store.creates == 1 and not store.uploads
    assert store.objects[case[1]["ciphertext_key"]] == case[2]


def test_daemon_uses_scoped_session_and_its_existing_journal(case, monkeypatch):
    from scripts.server_backup import backup_daemon, production_config
    from scripts.server_backup.target_credentials import publication_credentials
    from tests.deployment.test_server_backup_credentials import PARENT

    path, record, raw = case
    store = Store()
    store.close = lambda: None
    target = publication_credentials(PARENT, multipart=True)
    service = backup_daemon.BackupService.__new__(backup_daemon.BackupService)
    service.root = path.parent
    service.config = {
        "config_profile": "production",
        "publication_multipart_enabled": True,
        "target_environment_command": ["synthetic"],
        "target_endpoint": PARENT["endpoint"],
        "target_bucket": PARENT["bucket"],
        "target_prefix": production_config.PREFIX,
    }
    monkeypatch.setattr(backup_daemon, "environment", lambda _: target)
    monkeypatch.setattr(backup_daemon, "storage", lambda *args, **kwargs: store)
    result = service.publish(
        snapshot_id=record["snapshot_id"], ciphertext=raw, captured_at=record["captured_at"]
    )
    assert result == record
    with sqlite3.connect(path) as db:
        import json

        descriptor = json.loads(
            db.execute("SELECT descriptor FROM multipart_transfers").fetchone()[0]
        )
        assert descriptor["bucket"] == PARENT["bucket"]
    assert len(store.objects) == 2 and store.creates == 1


@pytest.mark.parametrize("fault", ["duplicate", "truncated", "unexpected_part", "wrong_bytes"])
def test_ambiguous_inventory_or_changed_parts_stop_without_completion(case, fault):
    store = Store()
    store.lose_create = True
    with pytest.raises(PublicationError):
        invoke(store, case)
    if fault == "duplicate":
        store.uploads["second"] = {"key": case[1]["ciphertext_key"], "parts": {}}
    elif fault == "truncated":
        store.list_multipart_uploads = lambda **kw: {"IsTruncated": True}
    elif fault == "unexpected_part":
        store.uploads["upload-1"]["parts"][3] = b"foreign"
    else:
        store.uploads["upload-1"]["parts"][1] = b"foreign"
    with pytest.raises(PublicationError):
        invoke(store, case)
    assert store.creates == 1 and store.complete_calls == 0 and not store.objects


@pytest.mark.parametrize("fail", [False, True])
def test_legacy_sdk_signs_conditional_complete_and_unregisters_on_failure(fail):
    from botocore.awsrequest import AWSRequest
    from botocore.hooks import HierarchicalEmitter
    from scripts.server_backup.multipart_publication import _complete

    events = HierarchicalEmitter()
    seen = []

    def complete(**kwargs):
        assert "IfNoneMatch" not in kwargs
        request = AWSRequest(method="POST", url="https://storage.example.test")
        events.emit("before-sign.s3.CompleteMultipartUpload", request=request)
        seen.append(request.headers["If-None-Match"])
        if fail:
            raise TimeoutError
        return {"ok": True}

    client = SimpleNamespace(
        meta=SimpleNamespace(
            events=events,
            service_model=SimpleNamespace(
                operation_model=lambda _: SimpleNamespace(input_shape=SimpleNamespace(members={}))
            ),
        ),
        complete_multipart_upload=complete,
    )
    if fail:
        with pytest.raises(TimeoutError):
            _complete(client, Bucket="test")
    else:
        assert _complete(client, Bucket="test") == {"ok": True}
    assert seen == ["*"]
    request = AWSRequest(method="POST", url="https://storage.example.test")
    events.emit("before-sign.s3.CompleteMultipartUpload", request=request)
    assert "If-None-Match" not in request.headers
