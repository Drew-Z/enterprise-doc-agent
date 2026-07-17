from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

import httpx
import pytest

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store import (
    Boto3MultipartObjectStore,
    ObjectStoreError,
    create_s3_client,
)

MIB = 1024**2
ALLOWED_ORIGIN = "http://127.0.0.1:5173"


def _checksum(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


@pytest.mark.integration
async def test_minio_checksum_multipart_round_trip_cors_and_range() -> None:
    settings = ObjectStoreSettings()
    adapter = Boto3MultipartObjectStore(settings=settings)
    control = create_s3_client(settings, endpoint_url=settings.endpoint)
    prefix = f"m1-probe/{uuid4().hex}/"
    key = f"{prefix}round-trip"
    upload_id: str | None = None
    completed = False
    first = b"a" * (5 * MIB)
    second = b"tail" * 1024
    try:
        upload_id = await adapter.create_upload(
            bucket=settings.documents_bucket,
            key=key,
            metadata={"contract": "m1"},
        )
        first_signed = await adapter.presign_upload_part(
            bucket=settings.documents_bucket,
            key=key,
            upload_id=upload_id,
            part_number=1,
            checksum_sha256_b64=_checksum(first),
            expires_in_seconds=settings.presign_ttl_seconds,
        )
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            preflight = await client.options(
                first_signed.url,
                headers={
                    "Origin": ALLOWED_ORIGIN,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "x-amz-checksum-sha256,content-type",
                },
            )
            assert preflight.status_code in {200, 204}
            assert preflight.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
            allowed_methods = {
                method.strip().upper()
                for method in preflight.headers["Access-Control-Allow-Methods"].split(",")
            }
            allowed_headers = {
                header.strip().lower()
                for header in preflight.headers["Access-Control-Allow-Headers"].split(",")
            }
            assert "PUT" in allowed_methods
            assert {"x-amz-checksum-sha256", "content-type"} <= allowed_headers
            evil = await client.options(
                first_signed.url,
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "x-amz-checksum-sha256",
                },
            )
            assert evil.headers.get("Access-Control-Allow-Origin") != "https://evil.example"
            bad_digest = await client.put(
                first_signed.url,
                headers={**first_signed.headers, "Origin": ALLOWED_ORIGIN},
                content=b"b" * len(first),
            )
            assert bad_digest.status_code >= 400
            first_put = await client.put(
                first_signed.url,
                headers={**first_signed.headers, "Origin": ALLOWED_ORIGIN},
                content=first,
            )
            assert first_put.status_code == 200
            assert first_put.headers.get("ETag")
            assert first_put.headers.get("x-amz-checksum-sha256") == _checksum(first)
            exposed_headers = first_put.headers["Access-Control-Expose-Headers"].lower()
            assert "etag" in exposed_headers
            assert any(
                value in exposed_headers for value in ("x-amz-checksum-sha256", "x-amz*", "*")
            )

            second_signed = await adapter.presign_upload_part(
                bucket=settings.documents_bucket,
                key=key,
                upload_id=upload_id,
                part_number=2,
                checksum_sha256_b64=_checksum(second),
                expires_in_seconds=settings.presign_ttl_seconds,
            )
            second_put = await client.put(
                second_signed.url,
                headers={**second_signed.headers, "Origin": ALLOWED_ORIGIN},
                content=second,
            )
            assert second_put.status_code == 200

        first_page = control.list_parts(
            Bucket=settings.documents_bucket,
            Key=key,
            UploadId=upload_id,
            MaxParts=1,
        )
        assert first_page["IsTruncated"] is True
        assert first_page["NextPartNumberMarker"] == 1
        listed = await adapter.list_parts(
            bucket=settings.documents_bucket,
            key=key,
            upload_id=upload_id,
        )
        assert [part.part_number for part in listed] == [1, 2]
        assert [part.checksum_sha256_b64 for part in listed] == [
            _checksum(first),
            _checksum(second),
        ]

        result = await adapter.complete_upload(
            bucket=settings.documents_bucket,
            key=key,
            upload_id=upload_id,
            parts=listed,
        )
        completed = True
        assert result.etag
        assert result.checksum_sha256_b64
        head = await adapter.head_object(bucket=settings.documents_bucket, key=key)
        assert head.size_bytes == len(first) + len(second)
        assert head.checksum_sha256_b64 == result.checksum_sha256_b64
        assert head.metadata == {"contract": "m1"}
        ranged = await adapter.get_range(
            bucket=settings.documents_bucket,
            key=key,
            start=len(first) - 4,
            end_inclusive=len(first) + 3,
        )
        assert ranged == first[-4:] + second[:4]
    finally:
        if upload_id is not None and not completed:
            try:
                await adapter.abort_upload(
                    bucket=settings.documents_bucket,
                    key=key,
                    upload_id=upload_id,
                )
            except ObjectStoreError:
                pass
        if completed:
            await adapter.delete_object(bucket=settings.documents_bucket, key=key)
        await adapter.close()
        control.close()


@pytest.mark.integration
async def test_minio_abort_and_incomplete_upload_pagination() -> None:
    settings = ObjectStoreSettings()
    adapter = Boto3MultipartObjectStore(settings=settings)
    prefix = f"m1-probe/{uuid4().hex}/"
    created: list[tuple[str, str]] = []
    try:
        for suffix in ("a", "b"):
            key = f"{prefix}{suffix}"
            upload_id = await adapter.create_upload(
                bucket=settings.documents_bucket,
                key=key,
                metadata={"contract": "m1"},
            )
            created.append((key, upload_id))

        uploads = await adapter.list_incomplete_uploads(
            bucket=settings.documents_bucket,
            prefix=prefix,
        )
        assert {(upload.key, upload.upload_id) for upload in uploads} == set(created)

        for key, upload_id in created:
            await adapter.abort_upload(
                bucket=settings.documents_bucket,
                key=key,
                upload_id=upload_id,
            )
        created.clear()
        assert (
            await adapter.list_incomplete_uploads(
                bucket=settings.documents_bucket,
                prefix=prefix,
            )
            == ()
        )
    finally:
        for key, upload_id in created:
            try:
                await adapter.abort_upload(
                    bucket=settings.documents_bucket,
                    key=key,
                    upload_id=upload_id,
                )
            except ObjectStoreError:
                pass
        await adapter.close()
