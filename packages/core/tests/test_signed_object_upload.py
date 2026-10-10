from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ReadTimeoutError
from botocore.httpsession import URLLib3Session
from pydantic import SecretStr

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store.client import create_s3_client
from enterprise_doc_core.object_store.errors import (
    ObjectStoreChecksumMismatch,
    ObjectStoreProtocolError,
    ObjectStoreRejected,
)
from enterprise_doc_core.object_store.signed_upload import Boto3SignedUploadWriter


class _ResponseBody:
    def __init__(self, data: bytes = b"") -> None:
        self.data = data

    def stream(self, *_args: Any, **_kwargs: Any) -> Iterator[bytes]:
        yield self.data


def _settings() -> ObjectStoreSettings:
    return ObjectStoreSettings(
        endpoint="https://objects.example.invalid",
        access_key=SecretStr("test-access"),
        secret_key=SecretStr("test-secret"),
    )


@pytest.mark.asyncio
async def test_signed_writer_sends_full_payload_sha256_and_create_only_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[dict[str, Any], bytes]] = []
    body = b"trusted upload content"

    def send(_self: Any, request: Any) -> AWSResponse:
        sent_body = request.body.read() if hasattr(request.body, "read") else request.body
        requests.append((dict(request.headers), sent_body))
        return AWSResponse(request.url, 200, {"ETag": '"storage-etag"'}, _ResponseBody())

    monkeypatch.setattr(URLLib3Session, "send", send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        receipt = await writer.write_content(
            bucket="documents",
            key="owned/session.txt",
            body=body,
            content_type="text/plain",
            metadata={"upload-session-id": "session"},
        )
    finally:
        await writer.close()

    assert len(requests) == 1
    headers, actual = requests[0]
    assert actual == body
    assert headers["X-Amz-Content-SHA256"] == hashlib.sha256(body).hexdigest().encode()
    assert headers["If-None-Match"] == b"*"
    assert b"x-amz-content-sha256" in headers["Authorization"]
    assert receipt.content_sha256 == hashlib.sha256(body).hexdigest()
    assert receipt.head.size_bytes == len(body)
    assert receipt.head.metadata == {"upload-session-id": "session"}
    assert receipt.head.checksum_sha256_b64 is None  # Not a stored-checksum claim.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [b"", b"x" * (1024 * 1024 + 1), bytearray(b"mutable")],
    ids=["empty", "over-limit", "mutable"],
)
async def test_signed_writer_rejects_unbounded_or_mutable_content_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    def unexpected_send(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("invalid content reached the transport")

    monkeypatch.setattr(URLLib3Session, "send", unexpected_send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        with pytest.raises(ObjectStoreProtocolError):
            await writer.write_content(
                bucket="documents",
                key="owned/key",
                body=body,
                content_type="text/plain",
                metadata={},
            )
    finally:
        await writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,etag", [(200, None), (200, ""), (202, '"etag"')])
async def test_signed_writer_does_not_accept_missing_or_nonterminal_receipt(
    monkeypatch: pytest.MonkeyPatch, status: int, etag: str | None
) -> None:
    def send(_self: Any, request: Any) -> AWSResponse:
        return AWSResponse(
            request.url, status, {} if etag is None else {"ETag": etag}, _ResponseBody()
        )

    monkeypatch.setattr(URLLib3Session, "send", send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        with pytest.raises(ObjectStoreProtocolError):
            await writer.write_content(
                bucket="documents",
                key="owned/key",
                body=b"content",
                content_type="text/plain",
                metadata={},
            )
    finally:
        await writer.close()


@pytest.mark.asyncio
async def test_signed_writer_leaves_existing_object_for_readback_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def send(_self: Any, request: Any) -> AWSResponse:
        nonlocal calls
        calls += 1
        return AWSResponse(
            request.url,
            412,
            {"Content-Type": "application/xml"},
            _ResponseBody(b"<Error><Code>PreconditionFailed</Code></Error>"),
        )

    monkeypatch.setattr(URLLib3Session, "send", send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        with pytest.raises(ObjectStoreRejected):
            await writer.write_content(
                bucket="documents",
                key="owned/key",
                body=b"content",
                content_type="text/plain",
                metadata={},
            )
    finally:
        await writer.close()
    assert calls == 1


@pytest.mark.asyncio
async def test_changed_payload_rejection_cannot_produce_a_write_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def send(_self: Any, request: Any) -> AWSResponse:
        nonlocal calls
        calls += 1
        original = request.body.read()
        assert (
            request.headers["X-Amz-Content-SHA256"] == hashlib.sha256(original).hexdigest().encode()
        )
        # Only the transport boundary is replaced. The real SDK signed the
        # original body; the storage boundary rejects changed received bytes.
        received = b"X" + original[1:]
        assert (
            hashlib.sha256(received).hexdigest().encode() != request.headers["X-Amz-Content-SHA256"]
        )
        return AWSResponse(
            request.url,
            400,
            {"Content-Type": "application/xml"},
            _ResponseBody(b"<Error><Code>XAmzContentSHA256Mismatch</Code></Error>"),
        )

    monkeypatch.setattr(URLLib3Session, "send", send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        with pytest.raises(ObjectStoreChecksumMismatch):
            await writer.write_content(
                bucket="documents",
                key="key",
                body=b"original",
                content_type="text/plain",
                metadata={},
            )
    finally:
        await writer.close()
    assert calls == 1


@pytest.mark.parametrize("sign_payload", [False, True])
def test_explicit_payload_signing_with_md5_preserves_other_client_defaults(
    monkeypatch: pytest.MonkeyPatch, sign_payload: bool
) -> None:
    import base64

    body = b"signed even when MD5 is supplied"
    sent: list[Any] = []

    def send(_self: Any, request: Any) -> AWSResponse:
        sent.append(request.headers["X-Amz-Content-SHA256"])
        return AWSResponse(request.url, 200, {"ETag": '"etag"'}, _ResponseBody())

    monkeypatch.setattr(URLLib3Session, "send", send)
    settings = _settings()
    client = create_s3_client(settings, endpoint_url=settings.endpoint, sign_payload=sign_payload)
    try:
        client.put_object(
            Bucket="documents",
            Key="key",
            Body=body,
            ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode(),
        )
    finally:
        client.close()
    assert sent == [
        hashlib.sha256(body).hexdigest().encode() if sign_payload else b"UNSIGNED-PAYLOAD"
    ]


@pytest.mark.asyncio
async def test_lost_write_response_then_precondition_failure_has_no_success_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import botocore.endpoint

    requests: list[tuple[str, bytes]] = []

    def send(_self: Any, request: Any) -> AWSResponse:
        body = request.body.read()
        requests.append((request.url, body))
        if len(requests) == 1:
            raise ReadTimeoutError(endpoint_url="https://private-value.invalid")
        return AWSResponse(
            request.url,
            412,
            {"Content-Type": "application/xml"},
            _ResponseBody(
                b"<Error><Code>PreconditionFailed</Code><Message>private-value</Message></Error>"
            ),
        )

    monkeypatch.setattr(URLLib3Session, "send", send)
    monkeypatch.setattr(botocore.endpoint.time, "sleep", lambda _delay: None)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        with pytest.raises(ObjectStoreRejected) as caught:
            await writer.write_content(
                bucket="documents",
                key="same-key",
                body=b"same-body",
                content_type="text/plain",
                metadata={},
            )
    finally:
        await writer.close()
    assert len(requests) == 2 and requests[0] == requests[1]
    assert "private-value" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_maximum_payload_is_signed_and_closed_writer_cannot_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"x" * 1024 * 1024
    calls = 0

    def send(_self: Any, request: Any) -> AWSResponse:
        nonlocal calls
        calls += 1
        assert request.headers["X-Amz-Content-SHA256"] == hashlib.sha256(body).hexdigest().encode()
        assert request.body.read() == body
        return AWSResponse(request.url, 200, {"ETag": '"etag"'}, _ResponseBody())

    monkeypatch.setattr(URLLib3Session, "send", send)
    writer = Boto3SignedUploadWriter(settings=_settings())
    try:
        receipt = await writer.write_content(
            bucket="documents", key="key", body=body, content_type="text/plain", metadata={}
        )
        assert receipt.head.size_bytes == len(body)
    finally:
        await writer.close()
    await writer.close()
    with pytest.raises(ObjectStoreProtocolError):
        await writer.write_content(
            bucket="documents", key="key", body=body, content_type="text/plain", metadata={}
        )
    assert calls == 1
