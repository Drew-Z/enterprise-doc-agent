"""Durable sealed backup retry and ownership contracts."""

import datetime
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.server_backup import backup_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-contract-")
        self.root = Path(self.temp.name)
        self.calls = {"capture": 0, "seal": 0, "upload": 0}
        self.fail_upload = False
        self.fail_capture = False
        self.captured_at = datetime.datetime.now(datetime.UTC).isoformat()
        self.ids = []

    def tearDown(self):
        self.temp.cleanup()

    def capture(self):
        self.calls["capture"] += 1
        if self.fail_capture:
            raise RuntimeError("private error must not escape")
        return {"captured_at": self.captured_at, "payload": b"synthetic"}

    def seal(self, captured):
        self.calls["seal"] += 1
        return b"age-encryption.org/v1\n" + captured["payload"] + b"-data"

    def upload(self, *, snapshot_id, ciphertext, captured_at):
        self.calls["upload"] += 1
        self.ids.append(snapshot_id)
        if self.fail_upload:
            raise TimeoutError("provider credential must not escape")
        return {
            "status": "ciphertext_upload_readback_verified",
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext),
            "actual_restore_verified": False,
        }

    def tick(self, fingerprint="stable-target"):
        return runtime.run_tick(
            root=self.root,
            config_fingerprint=fingerprint,
            capture=self.capture,
            seal=self.seal,
            publish=self.upload,
        )

    def test_failed_upload_resumes_exact_ciphertext_without_recapture(self):
        self.fail_upload = True
        self.assertEqual(self.tick()["status"], "upload_pending")
        self.assertIsNone(runtime.read_status(self.root)["last_success"])
        self.fail_upload = False
        result = self.tick()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.calls, {"capture": 1, "seal": 1, "upload": 2})
        self.assertEqual(self.ids[0], self.ids[1])
        self.assertEqual(
            runtime.read_status(self.root)["last_success"]["captured_at"], self.captured_at
        )
        self.assertFalse(list((self.root / "spool").iterdir()))

    def test_failure_preserves_last_success_and_config_drift_is_refused(self):
        self.assertEqual(self.tick()["status"], "succeeded")
        previous = runtime.read_status(self.root)["last_success"]
        self.fail_capture = True
        result = self.tick()
        self.assertEqual(result["status"], "capture_failed")
        self.assertNotIn("private error", str(result))
        self.assertEqual(runtime.read_status(self.root)["last_success"], previous)
        with self.assertRaises(runtime.RuntimeErrorSafe):
            self.tick("different-target")

    def test_lock_prevents_overlapping_capture_and_tampered_spool_is_retained(self):
        with runtime.runtime_lock(self.root) as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.tick()["status"], "busy")
            self.assertEqual(self.calls["capture"], 0)
        self.fail_upload = True
        self.assertEqual(self.tick()["status"], "upload_pending")
        path = next((self.root / "spool").iterdir())
        path.write_bytes(b"changed")
        result = self.tick()
        self.assertEqual(result["status"], "spool_integrity_failed")
        self.assertTrue(path.exists())
        self.assertEqual(self.calls["upload"], 1)


if __name__ == "__main__":
    unittest.main()
