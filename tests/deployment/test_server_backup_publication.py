"""Immutable publication and legacy SDK conditional signing."""

import io
import unittest
from types import SimpleNamespace

from botocore.exceptions import ClientError
from scripts.server_backup import server_publication as publish


class Storage:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.corrupt = False
        self.unknown_once = False

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ContentType):
        assert IfNoneMatch == "*"
        if (Bucket, Key) in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[Bucket, Key] = Body
        self.puts.append(Key)
        if self.unknown_once:
            self.unknown_once = False
            raise TimeoutError("acknowledgement lost")
        return {}

    def get_object(self, *, Bucket, Key):
        raw = self.objects[Bucket, Key]
        if self.corrupt:
            raw = raw[:-1] + bytes([raw[-1] ^ 1])
        return {"ContentLength": len(raw), "Body": io.BytesIO(raw)}


class PublicationTests(unittest.TestCase):
    def invoke(self, store, data=b"age-encryption.org/v1\nsynthetic-ciphertext"):
        return publish.publish_snapshot(
            client=store,
            bucket="recovery-test",
            prefix="operations-recovery/v1/",
            snapshot_id="snapshot-20261002-001",
            ciphertext=data,
            captured_at="2026-10-02T08:00:00+00:00",
        )

    def test_complete_upload_is_read_back_and_same_snapshot_is_idempotent(self):
        store = Storage()
        result = self.invoke(store)
        self.assertEqual(result["status"], "ciphertext_upload_readback_verified")
        self.assertFalse(result["actual_restore_verified"])
        self.assertEqual(result["captured_at"], "2026-10-02T08:00:00+00:00")
        self.assertEqual(len(store.puts), 2)
        self.assertEqual(self.invoke(store), result)
        self.assertEqual(len(store.puts), 2)
        with self.assertRaises(publish.PublicationError):
            self.invoke(store, b"age-encryption.org/v1\nchanged")
        self.assertEqual(len(store.puts), 2)

    def test_corrupt_readback_never_publishes_completion_marker(self):
        store = Storage()
        store.corrupt = True
        with self.assertRaises(publish.PublicationError):
            self.invoke(store)
        self.assertEqual(len(store.puts), 1)
        self.assertTrue(store.puts[0].endswith(".tar.age"))

    def test_unknown_acknowledgement_is_not_success_and_can_resume_exact_input(self):
        store = Storage()
        store.unknown_once = True
        with self.assertRaises(publish.PublicationError):
            self.invoke(store)
        self.assertEqual(len(store.puts), 1)
        self.assertEqual(self.invoke(store)["status"], "ciphertext_upload_readback_verified")
        self.assertEqual(len(store.puts), 2)

    def test_older_sdk_signs_conditional_header_and_unregisters_callback(self):
        class Events:
            def __init__(self):
                self.handlers = {}

            def register(self, event, handler, unique_id):
                self.handlers[unique_id] = handler

            def unregister(self, event, unique_id):
                self.handlers.pop(unique_id)

        class LegacyStorage(Storage):
            def __init__(self):
                super().__init__()
                self.meta = SimpleNamespace(
                    events=Events(),
                    service_model=SimpleNamespace(
                        operation_model=lambda name: SimpleNamespace(
                            input_shape=SimpleNamespace(members={})
                        )
                    ),
                )

            def put_object(self, *, Bucket, Key, Body, ContentType):
                request = SimpleNamespace(headers={})
                for handler in self.meta.events.handlers.values():
                    handler(request=request)
                return super().put_object(
                    Bucket=Bucket,
                    Key=Key,
                    Body=Body,
                    ContentType=ContentType,
                    IfNoneMatch=request.headers.get("If-None-Match"),
                )

        store = LegacyStorage()
        self.assertEqual(self.invoke(store)["status"], "ciphertext_upload_readback_verified")
        self.assertEqual(self.invoke(store)["status"], "ciphertext_upload_readback_verified")
        self.assertEqual(store.meta.events.handlers, {})
        self.assertEqual(len(store.puts), 2)


if __name__ == "__main__":
    unittest.main()
