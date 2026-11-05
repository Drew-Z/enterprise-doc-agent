from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store.client import create_s3_client
from enterprise_doc_core.object_store.errors import ObjectStoreProtocolError
from enterprise_doc_core.object_store.multipart import Boto3MultipartObjectStore


async def test_direct_put_signature_binds_no_overwrite_size_and_identity() -> None:
    settings = ObjectStoreSettings(presign_ttl_seconds=60)
    client = create_s3_client(settings, endpoint_url="https://storage.example.test")
    adapter = Boto3MultipartObjectStore(
        settings=settings, control_client=client, presign_client=client
    )
    try:
        signed = await adapter.presign_object_put(
            bucket="documents",
            key="uploads/unique-session",
            size_bytes=25,
            metadata={"upload-session-id": "session-123", "declared-size": "25"},
            expires_in_seconds=120,
        )
        query = parse_qs(urlparse(signed.url).query)
        assert set(query["X-Amz-SignedHeaders"][0].split(";")) == {
            "host",
            "content-length",
            "if-none-match",
            "x-amz-meta-upload-session-id",
            "x-amz-meta-declared-size",
        }
        assert query["X-Amz-Expires"] == ["60"]
        assert signed.expires_in_seconds == 60
        assert signed.headers == {
            "Content-Length": "25",
            "If-None-Match": "*",
            "x-amz-meta-upload-session-id": "session-123",
            "x-amz-meta-declared-size": "25",
        }
    finally:
        await adapter.close()


@pytest.mark.parametrize("size", [0, -1, True, 1.5, "25"])
async def test_direct_put_rejects_invalid_size_before_signing(size: object) -> None:
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(), control_client=object(), presign_client=object()
    )
    with pytest.raises(ObjectStoreProtocolError):
        await adapter.presign_object_put(
            bucket="documents",
            key="key",
            size_bytes=size,
            metadata={"contract": "upload"},
            expires_in_seconds=60,
        )


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5])
async def test_direct_put_rejects_invalid_ttl_before_signing(ttl: object) -> None:
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(), control_client=object(), presign_client=object()
    )
    with pytest.raises(ObjectStoreProtocolError):
        await adapter.presign_object_put(
            bucket="documents",
            key="key",
            size_bytes=25,
            metadata={"contract": "upload"},
            expires_in_seconds=ttl,
        )


class RetirementClient:
    def __init__(self, *, exists=True, foreign=False, race=False, retired=False):
        self.identity = {
            "contract": "m1",
            "upload-session-id": "s",
            "version-id": "v",
            "declared-size": "25",
        }
        self.metadata = {**self.identity}
        if foreign:
            self.metadata["version-id"] = "foreign"
        if retired:
            self.metadata["upload-retired"] = "true"
        self.exists = exists
        self.race = race
        self.size = 0 if retired else 25
        self.puts = []

    def head_object(self, **kwargs):
        if not self.exists:
            raise self.error(404)
        return {"ContentLength": self.size, "ETag": "etag-original", "Metadata": self.metadata}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        if self.race:
            raise self.error(412)
        self.exists = True
        self.size = 0
        self.metadata = kwargs["Metadata"]
        return {"ETag": "etag-retired"}

    @staticmethod
    def error(status):
        return ClientError(
            {"Error": {"Code": str(status)}, "ResponseMetadata": {"HTTPStatusCode": status}},
            "PutObject",
        )


@pytest.mark.parametrize("exists", [True, False])
async def test_retirement_erases_content_and_keeps_idempotent_create_only_barrier(exists):
    client = RetirementClient(exists=exists)
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(), control_client=client, presign_client=client
    )
    assert await adapter.retire_upload_object(
        bucket="documents", key="key", metadata=client.identity
    )
    assert client.puts[0]["Body"] == b"" and client.puts[0]["ContentLength"] == 0
    assert client.puts[0].get("IfMatch" if exists else "IfNoneMatch") == (
        "etag-original" if exists else "*"
    )
    assert await adapter.retire_upload_object(
        bucket="documents", key="key", metadata=client.identity
    )
    assert len(client.puts) == 1


async def test_retirement_never_overwrites_foreign_object():
    client = RetirementClient(foreign=True)
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(), control_client=client, presign_client=client
    )
    with pytest.raises(ObjectStoreProtocolError):
        await adapter.retire_upload_object(bucket="documents", key="key", metadata=client.identity)
    assert not client.puts


async def test_retirement_reports_conditional_race_for_durable_retry():
    client = RetirementClient(race=True)
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(), control_client=client, presign_client=client
    )
    assert not await adapter.retire_upload_object(
        bucket="documents", key="key", metadata=client.identity
    )
    assert client.size == 25 and len(client.puts) == 1
