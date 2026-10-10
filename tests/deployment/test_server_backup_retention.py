"""Exercise real budget/planner/runtime logic at the S3 boundary only."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError
from scripts.server_backup import backup_runtime
from scripts.server_backup import remote_retention as remote
from scripts.server_backup.server_publication import marker_bytes, snapshot_record

BUCKET = "recovery-test"
PREFIX = remote.PREFIX


class Storage:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.page_size = 1000
        self.repeat_token = False
        self.reject_marker_read = False

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        end = start + min(MaxKeys, self.page_size)
        rows = [
            {"Key": k, "Size": len(self.objects[k]), "ETag": self.etag(k)} for k in keys[start:end]
        ]
        result = {"Contents": rows, "IsTruncated": end < len(keys)}
        if result["IsTruncated"]:
            result["NextContinuationToken"] = "1" if self.repeat_token else str(end)
        return result

    def etag(self, key):
        return '"' + hashlib.sha256(self.objects[key]).hexdigest() + '"'

    def get_object(self, *, Bucket, Key, IfMatch=None):
        if IfMatch and (self.reject_marker_read or IfMatch != self.etag(Key)):
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "GetObject",
            )
        raw = self.objects[Key]
        return {"Body": io.BytesIO(raw), "ContentLength": len(raw), "ETag": self.etag(Key)}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ContentType):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[Key] = Body
        self.puts.append(Key)


def kwargs(number=1):
    return {
        "snapshot_id": "snapshot-test-" + str(number),
        "ciphertext": b"age-encryption.org/v1\nsynthetic-" + str(number).encode() + b"-ciphertext",
        "captured_at": f"2026-10-02T08:{number:02d}:00+00:00",
    }


def seed(store, number, content=None):
    args = kwargs(number)
    record = snapshot_record(bucket=BUCKET, prefix=PREFIX, **args)
    store.objects[record["ciphertext_key"]] = args["ciphertext"]
    store.objects[PREFIX + args["snapshot_id"] + ".complete.json"] = marker_bytes(record)
    proof = {
        "status": "actual_restore_verified",
        "ciphertext_sha256": record["ciphertext_sha256"],
        "content_sha256": hashlib.sha256(str(content or number).encode()).hexdigest(),
        "evidence_sha256": hashlib.sha256(("evidence-" + str(number)).encode()).hexdigest(),
        "database_fingerprints_match": True,
        "object_bytes_match": True,
    }
    return args["snapshot_id"], proof


def plan(store, proofs=None, protected=frozenset()):
    return remote.plan_retention(
        client=store,
        bucket=BUCKET,
        prefix=PREFIX,
        restore_proofs={} if proofs is None else proofs,
        protected_ids=protected,
        at="2026-10-02T10:00:00+00:00",
    )


class RetentionTests(unittest.TestCase):
    def test_budget_rejects_before_any_put_and_exact_full_cap_replay_succeeds(self):
        store = Storage()
        args = kwargs()
        record = snapshot_record(bucket=BUCKET, prefix=PREFIX, **args)
        exact = len(args["ciphertext"]) + len(marker_bytes(record))
        with self.assertRaises(remote.RemoteBudgetExceeded):
            remote.publish_budgeted_snapshot(
                client=store, bucket=BUCKET, prefix=PREFIX, max_bytes=exact - 1, **args
            )
        self.assertEqual(store.puts, [])
        result = remote.publish_budgeted_snapshot(
            client=store, bucket=BUCKET, prefix=PREFIX, max_bytes=exact, **args
        )
        self.assertEqual(result, record)
        self.assertEqual(
            remote.publish_budgeted_snapshot(
                client=store, bucket=BUCKET, prefix=PREFIX, max_bytes=exact, **args
            ),
            result,
        )
        self.assertEqual(len(store.puts), 2)

    def test_unknown_ciphertext_resumes_with_only_marker_bytes_reserved(self):
        store = Storage()
        args = kwargs()
        record = snapshot_record(bucket=BUCKET, prefix=PREFIX, **args)
        store.objects[record["ciphertext_key"]] = args["ciphertext"]
        exact = len(args["ciphertext"]) + len(marker_bytes(record))
        result = remote.publish_budgeted_snapshot(
            client=store, bucket=BUCKET, prefix=PREFIX, max_bytes=exact, **args
        )
        self.assertEqual(result, record)
        self.assertEqual(store.puts, [PREFIX + args["snapshot_id"] + ".complete.json"])

    def test_budget_failure_preserves_runtime_id_bytes_and_last_success(self):
        store = Storage()
        captures = []

        def capture():
            captures.append(True)
            return {"captured_at": kwargs()["captured_at"]}

        with tempfile.TemporaryDirectory(prefix="docagent-retention-contract-") as root:

            def tick(cap):
                return backup_runtime.run_tick(
                    root=root,
                    config_fingerprint="same-test-target",
                    capture=capture,
                    seal=lambda _: kwargs()["ciphertext"],
                    publish=lambda **args: remote.publish_budgeted_snapshot(
                        client=store, bucket=BUCKET, prefix=PREFIX, max_bytes=cap, **args
                    ),
                )

            first = tick(10000)
            self.assertEqual(first["status"], "succeeded")
            previous = backup_runtime.read_status(root)["last_success"]
            failed = tick(sum(len(v) for v in store.objects.values()))
            self.assertEqual(failed["status"], "upload_pending")
            self.assertEqual(failed["error_type"], "RemoteBudgetExceeded")
            pending = Path(root) / "spool" / (failed["snapshot_id"] + ".tar.age")
            raw = pending.read_bytes()
            self.assertEqual(backup_runtime.read_status(root)["last_success"], previous)
            resumed = tick(10000)
            self.assertEqual(resumed["snapshot_id"], failed["snapshot_id"])
            self.assertEqual(resumed["status"], "succeeded")
            self.assertEqual(store.objects[PREFIX + failed["snapshot_id"] + ".tar.age"], raw)
            self.assertEqual(len(captures), 2)
            self.assertFalse(pending.exists())

    def test_new_unverified_uploads_cannot_displace_five_restored_contents(self):
        store = Storage()
        proofs = dict(seed(store, n) for n in range(1, 9))
        for n in range(9, 16):
            seed(store, n)
        result = plan(store, proofs, frozenset({"snapshot-test-1"}))
        self.assertEqual(
            {r["snapshot_id"] for r in result["candidates"]}, {"snapshot-test-2", "snapshot-test-3"}
        )
        retained = {r["snapshot_id"]: r for r in result["retained"]}
        for n in range(4, 9):
            self.assertIn("latest_distinct_content", retained[f"snapshot-test-{n}"]["reasons"])
        for n in range(9, 16):
            self.assertIn("actual_restore_unverified", retained[f"snapshot-test-{n}"]["reasons"])
        self.assertFalse(result["approval_granted"])
        self.assertEqual(result["remote_mutations"], 0)
        self.assertEqual(store.puts, [])

    def test_duplicate_content_keeps_latest_instance_and_all_referenced_ones(self):
        store = Storage()
        proofs = dict(seed(store, n) for n in range(1, 7))
        proofs.update(dict(seed(store, n, content=6) for n in range(7, 11)))
        result = plan(store, proofs, frozenset({"snapshot-test-7"}))
        ids = {r["snapshot_id"] for r in result["retained"]}
        self.assertTrue(
            {
                "snapshot-test-2",
                "snapshot-test-3",
                "snapshot-test-4",
                "snapshot-test-5",
                "snapshot-test-10",
                "snapshot-test-7",
            }
            <= ids
        )
        self.assertEqual(
            {r["snapshot_id"] for r in result["candidates"]},
            {"snapshot-test-1", "snapshot-test-6", "snapshot-test-8", "snapshot-test-9"},
        )

    def test_incomplete_invalid_marker_and_false_restore_claim_are_protected(self):
        store = Storage()
        for n in range(1, 8):
            seed(store, n)
        store.objects.pop(PREFIX + "snapshot-test-1.complete.json")
        store.objects[PREFIX + "snapshot-test-2.complete.json"] = b"not json"
        key = PREFIX + "snapshot-test-3.complete.json"
        record = json.loads(store.objects[key])
        record["actual_restore_verified"] = True
        store.objects[key] = marker_bytes(record)
        result = plan(store)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["retained"]), 7)

    def test_future_and_mismatched_proof_never_become_candidates(self):
        store = Storage()
        proofs = dict(seed(store, n) for n in range(1, 9))
        proofs["snapshot-test-1"]["ciphertext_sha256"] = "0" * 64
        key = PREFIX + "snapshot-test-2.complete.json"
        record = json.loads(store.objects[key])
        record["captured_at"] = "2027-01-01T00:00:00+00:00"
        store.objects[key] = marker_bytes(record)
        result = plan(store, proofs)
        retained = {r["snapshot_id"]: r for r in result["retained"]}
        self.assertIn("actual_restore_unverified", retained["snapshot-test-1"]["reasons"])
        self.assertIn("invalid_or_changed_marker", retained["snapshot-test-2"]["reasons"])

    def test_scoped_pagination_and_unknown_keys_fail_without_mutations(self):
        store = Storage()
        for n in range(1, 8):
            seed(store, n)
        store.page_size = 3
        store.objects["unrelated-outside-prefix"] = b"untouched"
        self.assertEqual(len(remote.inventory(client=store, bucket=BUCKET, prefix=PREFIX)), 14)
        store.objects[PREFIX + "unrelated-inside-prefix"] = b"unknown"
        with self.assertRaises(remote.RemoteInventoryError):
            plan(store)
        self.assertEqual(store.puts, [])

    def test_repeated_page_token_and_changed_marker_abort(self):
        store = Storage()
        for n in range(1, 8):
            seed(store, n)
        store.page_size = 2
        store.repeat_token = True
        with self.assertRaises(remote.RemoteInventoryError):
            plan(store)
        store.page_size = 1000
        store.repeat_token = False
        store.reject_marker_read = True
        with self.assertRaises(ClientError):
            plan(store)
        self.assertEqual(store.puts, [])

    def test_service_uses_budget_guard_without_changing_receipt_contract(self):
        from scripts.server_backup import backup_daemon

        store = Storage()
        store.close = lambda: None
        service = backup_daemon.BackupService.__new__(backup_daemon.BackupService)
        service.config = {
            "target_environment_command": ["synthetic-only"],
            "target_endpoint": "http://127.0.0.1:9000",
            "target_bucket": BUCKET,
            "target_prefix": PREFIX,
            "remote_max_bytes": 1,
        }
        target = {
            "endpoint": service.config["target_endpoint"],
            "bucket": BUCKET,
            "access": "synthetic",
            "secret": "synthetic",
        }
        with (
            patch.object(backup_daemon, "environment", return_value=target),
            patch.object(backup_daemon, "storage", return_value=store),
        ):
            with self.assertRaises(remote.RemoteBudgetExceeded):
                service.publish(**kwargs())
        self.assertEqual(store.puts, [])

    def test_lower_retention_or_oversized_inventory_is_rejected(self):
        store = Storage()
        with self.assertRaises(remote.RemoteInventoryError):
            remote.plan_retention(
                client=store,
                bucket=BUCKET,
                prefix=PREFIX,
                restore_proofs={},
                protected_ids=frozenset(),
                keep_distinct=4,
            )
        for n in range(4097):
            store.objects[PREFIX + f"snapshot-item-{n}.tar.age"] = b"x"
        with self.assertRaises(remote.RemoteInventoryError):
            remote.inventory(client=store, bucket=BUCKET, prefix=PREFIX)


if __name__ == "__main__":
    unittest.main()
