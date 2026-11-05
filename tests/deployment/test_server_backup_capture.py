from __future__ import annotations

import hashlib
import io
import threading

import pytest
from scripts.server_backup.server_capture import CaptureError, capture_objects


def reference(key, content):
    return {
        "reference_type": "document_version",
        "reference_id": key,
        "key": key,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


class Store:
    def __init__(self, values, failures=()):
        self.values = values
        self.failures = set(failures)
        self.calls = []
        self.lock = threading.Lock()

    def get_object(self, *, Bucket, Key):
        with self.lock:
            self.calls.append((Bucket, Key))
        if Key in self.failures:
            raise OSError("controlled source interruption")
        raw = self.values[Key]
        return {"ContentLength": len(raw), "Body": io.BytesIO(raw)}


def capture(store, references, cache):
    return capture_objects(
        client=store,
        references=references,
        documents_bucket="source",
        allowed_buckets={"source"},
        cache=cache,
    )


def test_interrupted_capture_reuses_only_individually_verified_objects():
    data = {"one": b"one", "two": b"two", "three": b"three"}
    refs = [reference(key, raw) for key, raw in data.items()]
    cache = {}
    first = Store(data, failures={"two"})
    with pytest.raises(CaptureError):
        capture(first, refs, cache)
    assert {key[1] for key in cache} == {"one", "three"}
    retry = Store(data)
    result = capture(retry, refs, cache)
    assert retry.calls == [("source", "two")]
    assert result["objects"] == 3 and result["source_gets"] == 1
    assert set(result["payload"].values()) >= set(data.values())


def test_corrupt_object_never_enters_reusable_cache():
    refs = [reference("good", b"safe"), reference("bad", b"correct")]
    cache = {}
    with pytest.raises(CaptureError):
        capture(Store({"good": b"safe", "bad": b"corrupt"}), refs, cache)
    assert {key[1] for key in cache} == {"good"}
    retry = Store({"good": b"safe", "bad": b"correct"})
    assert capture(retry, refs, cache)["source_gets"] == 1
    assert retry.calls == [("source", "bad")]


def test_reference_changes_prune_obsolete_cache_and_revalidate_existing_bytes():
    old = b"previous"
    cache = {("source", "old", hashlib.sha256(old).hexdigest(), len(old)): old}
    correct = b"same-size"
    key = ("source", "new", hashlib.sha256(correct).hexdigest(), len(correct))
    cache[key] = b"corrupted"
    refs = [reference("new", correct)]
    store = Store({"new": correct})
    assert capture(store, refs, cache)["source_gets"] == 1
    assert cache == {key: correct}
