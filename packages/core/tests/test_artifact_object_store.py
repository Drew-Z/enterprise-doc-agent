from __future__ import annotations

import base64
import hashlib
import threading
from typing import Any

import pytest

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store import (
    Boto3ArtifactObjectStore,
    ObjectStoreChecksumMismatch,
    ObjectStoreProtocolError,
)


class RecordingArtifactClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.body = b""
        self.metadata: dict[str, str] = {}
        self.content_type = "application/json"
        self.closed = 0

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs, threading.get_ident()))

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("put_object", kwargs)
        self.body = kwargs["Body"]
        self.metadata = kwargs["Metadata"]
        self.content_type = kwargs["ContentType"]
        return {"ETag": '"artifact"'}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("head_object", kwargs)
        checksum = base64.b64encode(hashlib.sha256(self.body).digest()).decode("ascii")
        return {
            "ContentLength": len(self.body),
            "ETag": '"artifact"',
            "ChecksumSHA256": checksum,
            "ContentType": self.content_type,
            "Metadata": self.metadata,
        }

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_object", kwargs)
        return {}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self._record("generate_presigned_url", kwargs)
        return "http://browser-store.test/signed-artifact"

    def close(self) -> None:
        self.closed += 1


async def test_artifact_adapter_puts_with_checksum_and_heads_after_write() -> None:
    control = RecordingArtifactClient()
    presign = RecordingArtifactClient()
    adapter = Boto3ArtifactObjectStore(
        settings=ObjectStoreSettings(presign_ttl_seconds=120),
        control_client=control,
        presign_client=presign,
        max_object_bytes=1024,
    )
    event_loop_thread = threading.get_ident()

    result = await adapter.put_object(
        bucket="artifacts",
        key="tenant/run/answer.json",
        body=b"{}",
        content_type="application/json",
        metadata={"kind": "answer"},
    )

    assert result.content_sha256 == hashlib.sha256(b"{}").hexdigest()
    assert control.calls[0][0] == "put_object"
    assert control.calls[0][1]["ChecksumSHA256"] == base64.b64encode(
        hashlib.sha256(b"{}").digest()
    ).decode("ascii")
    assert control.calls[0][1]["Metadata"]["sha256"] == result.content_sha256
    assert control.calls[0][2] != event_loop_thread
    assert control.calls[1][0] == "head_object"


async def test_artifact_adapter_caps_get_ttl_and_deletes_without_logging_body() -> None:
    control = RecordingArtifactClient()
    presign = RecordingArtifactClient()
    adapter = Boto3ArtifactObjectStore(
        settings=ObjectStoreSettings(presign_ttl_seconds=90),
        control_client=control,
        presign_client=presign,
    )

    signed = await adapter.presign_get(
        bucket="artifacts",
        key="tenant/run/answer.json",
        expires_in_seconds=900,
    )
    await adapter.delete_object(bucket="artifacts", key="tenant/run/answer.json")

    assert signed.expires_in_seconds == 90
    assert presign.calls[0][1] == {
        "ClientMethod": "get_object",
        "Params": {"Bucket": "artifacts", "Key": "tenant/run/answer.json"},
        "ExpiresIn": 90,
        "HttpMethod": "GET",
    }
    assert control.calls[0][0] == "delete_object"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bucket": "", "key": "a", "body": b"x", "content_type": "text/plain"},
        {"bucket": "artifacts", "key": "../a", "body": b"x", "content_type": "text/plain"},
        {
            "bucket": "artifacts",
            "key": "a",
            "body": b"x" * 5,
            "content_type": "text/plain",
        },
    ],
)
async def test_artifact_adapter_rejects_unbounded_locations_and_bodies(
    kwargs: dict[str, object],
) -> None:
    adapter = Boto3ArtifactObjectStore(
        settings=ObjectStoreSettings(),
        control_client=RecordingArtifactClient(),
        presign_client=RecordingArtifactClient(),
        max_object_bytes=4,
    )
    with pytest.raises(ObjectStoreProtocolError):
        await adapter.put_object(**kwargs)  # type: ignore[arg-type]


async def test_artifact_adapter_rejects_post_write_integrity_mismatch() -> None:
    control = RecordingArtifactClient()

    def bad_head(**kwargs: Any) -> dict[str, Any]:
        response = RecordingArtifactClient.head_object(control, **kwargs)
        response["Metadata"] = {"sha256": "wrong"}
        return response

    control.head_object = bad_head  # type: ignore[method-assign]
    adapter = Boto3ArtifactObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingArtifactClient(),
    )
    with pytest.raises(ObjectStoreChecksumMismatch):
        await adapter.put_object(
            bucket="artifacts",
            key="tenant/run/answer.json",
            body=b"{}",
            content_type="application/json",
        )
