from __future__ import annotations

import base64
import threading
import traceback
from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store import (
    Boto3MultipartObjectStore,
    ObjectStoreNotFound,
    ObjectStoreProtocolError,
    UploadedPart,
    create_s3_client,
)


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False
        self.thread_ids: list[int] = []

    def read(self, _: int) -> bytes:
        self.thread_ids.append(threading.get_ident())
        return self.content

    def close(self) -> None:
        self.closed = True


class RecordingS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.list_part_pages: list[dict[str, Any]] = []
        self.list_upload_pages: list[dict[str, Any]] = []
        self.body = FakeBody(b"range-bytes")
        self.closed = 0
        self.error: Exception | None = None

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs, threading.get_ident()))
        if self.error is not None:
            error = self.error
            self.error = None
            raise error

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._record("create_multipart_upload", kwargs)
        return {"UploadId": "upload-id"}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self._record("generate_presigned_url", kwargs)
        return "http://browser-store.test/signed-part"

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_parts", kwargs)
        return self.list_part_pages.pop(0)

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._record("complete_multipart_upload", kwargs)
        return {"ETag": '"completed"', "ChecksumSHA256": "combined-2"}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("head_object", kwargs)
        return {
            "ContentLength": 11,
            "ETag": '"completed"',
            "ChecksumSHA256": "combined-2",
            "ContentType": "application/pdf",
            "Metadata": {"contract": "m1"},
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_object", kwargs)
        byte_range = str(kwargs["Range"]).removeprefix("bytes=")
        end = int(byte_range.split("-", maxsplit=1)[1])
        return {
            "Body": self.body,
            "ContentLength": len(self.body.content),
            "ContentRange": f"bytes {byte_range}/{end + 1}",
        }

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._record("abort_multipart_upload", kwargs)
        return {}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_object", kwargs)
        return {}

    def list_multipart_uploads(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_multipart_uploads", kwargs)
        return self.list_upload_pages.pop(0)

    def close(self) -> None:
        self.closed += 1


def _checksum(value: bytes = b"part") -> str:
    return base64.b64encode(value.ljust(32, b"x")[:32]).decode("ascii")


async def test_adapter_offloads_create_and_checksum_bound_presign() -> None:
    control = RecordingS3Client()
    presign = RecordingS3Client()
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=presign,
    )
    event_loop_thread = threading.get_ident()

    upload_id = await adapter.create_upload(
        bucket="documents",
        key="m1/uploads/random",
        metadata={"contract": "m1"},
    )
    signed = await adapter.presign_upload_part(
        bucket="documents",
        key="m1/uploads/random",
        upload_id=upload_id,
        part_number=1,
        checksum_sha256_b64=_checksum(),
        expires_in_seconds=120,
    )

    assert upload_id == "upload-id"
    create_call = control.calls[0]
    assert create_call[0] == "create_multipart_upload"
    assert create_call[1] == {
        "Bucket": "documents",
        "Key": "m1/uploads/random",
        "ChecksumAlgorithm": "SHA256",
        "Metadata": {"contract": "m1"},
    }
    assert create_call[2] != event_loop_thread
    presign_call = presign.calls[0]
    assert presign_call[0] == "generate_presigned_url"
    assert presign_call[1]["ClientMethod"] == "upload_part"
    assert presign_call[1]["Params"]["ChecksumSHA256"] == _checksum()
    assert presign_call[1]["ExpiresIn"] == 120
    assert presign_call[1]["HttpMethod"] == "PUT"
    assert signed.headers == {"x-amz-checksum-sha256": _checksum()}
    assert signed.expires_in_seconds == 120


async def test_adapter_caps_and_validates_requested_presign_ttl() -> None:
    client = RecordingS3Client()
    settings = ObjectStoreSettings(presign_ttl_seconds=300)
    adapter = Boto3MultipartObjectStore(
        settings=settings,
        control_client=client,
        presign_client=client,
    )

    signed = await adapter.presign_upload_part(
        bucket="documents",
        key="m1/uploads/random",
        upload_id="upload-id",
        part_number=1,
        checksum_sha256_b64=_checksum(),
        expires_in_seconds=900,
    )

    assert client.calls[0][1]["ExpiresIn"] == 300
    assert signed.expires_in_seconds == 300
    for invalid_ttl in (0, True, 1.5, "60", None):
        with pytest.raises(ObjectStoreProtocolError):
            await adapter.presign_upload_part(
                bucket="documents",
                key="m1/uploads/random",
                upload_id="upload-id",
                part_number=1,
                checksum_sha256_b64=_checksum(),
                expires_in_seconds=invalid_ttl,
            )


async def test_adapter_collects_all_part_and_incomplete_upload_pages() -> None:
    control = RecordingS3Client()
    control.list_part_pages = [
        {
            "IsTruncated": True,
            "NextPartNumberMarker": 1,
            "Parts": [
                {
                    "PartNumber": 1,
                    "Size": 5,
                    "ETag": '"one"',
                    "ChecksumSHA256": "checksum-one",
                }
            ],
        },
        {
            "IsTruncated": False,
            "Parts": [
                {
                    "PartNumber": 2,
                    "Size": 3,
                    "ETag": '"two"',
                    "ChecksumSHA256": "checksum-two",
                }
            ],
        },
    ]
    control.list_upload_pages = [
        {
            "IsTruncated": True,
            "NextKeyMarker": "prefix/b",
            "NextUploadIdMarker": "upload-b",
            "Uploads": [
                {
                    "Key": "prefix/a",
                    "UploadId": "upload-a",
                    "Initiated": datetime(2026, 7, 17, tzinfo=UTC),
                }
            ],
        },
        {
            "IsTruncated": False,
            "Uploads": [
                {
                    "Key": "prefix/b",
                    "UploadId": "upload-b",
                    "Initiated": datetime(2026, 7, 17, tzinfo=UTC),
                }
            ],
        },
    ]
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingS3Client(),
    )

    parts = await adapter.list_parts(
        bucket="documents",
        key="prefix/object",
        upload_id="upload-id",
    )
    uploads = await adapter.list_incomplete_uploads(bucket="documents", prefix="prefix/")

    assert [part.part_number for part in parts] == [1, 2]
    assert parts[1].checksum_sha256_b64 == "checksum-two"
    assert control.calls[1][1]["PartNumberMarker"] == 1
    assert [upload.upload_id for upload in uploads] == ["upload-a", "upload-b"]
    assert control.calls[3][1]["KeyMarker"] == "prefix/b"
    assert control.calls[3][1]["UploadIdMarker"] == "upload-b"


async def test_adapter_falls_back_when_store_ignores_directory_prefix() -> None:
    control = RecordingS3Client()
    control.list_upload_pages = [
        {"IsTruncated": False, "Uploads": []},
        {
            "IsTruncated": False,
            "Uploads": [
                {"Key": "other/object", "UploadId": "other"},
                {"Key": "prefix/a", "UploadId": "upload-a"},
                {"Key": "prefix/b", "UploadId": "upload-b"},
            ],
        },
    ]
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingS3Client(),
    )

    uploads = await adapter.list_incomplete_uploads(
        bucket="documents",
        prefix="prefix/",
    )

    assert [upload.upload_id for upload in uploads] == ["upload-a", "upload-b"]
    assert control.calls[0][1]["Prefix"] == "prefix/"
    assert "Prefix" not in control.calls[1][1]


async def test_adapter_completes_heads_reads_ranges_and_closes_once() -> None:
    control = RecordingS3Client()
    presign = RecordingS3Client()
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=presign,
    )
    parts = (
        UploadedPart(1, 5, '"one"', "checksum-one"),
        UploadedPart(2, 3, '"two"', "checksum-two"),
    )

    completed = await adapter.complete_upload(
        bucket="documents",
        key="prefix/object",
        upload_id="upload-id",
        parts=parts,
    )
    head = await adapter.head_object(bucket="documents", key="prefix/object")
    content = await adapter.get_range(
        bucket="documents",
        key="prefix/object",
        start=10,
        end_inclusive=20,
    )
    await adapter.abort_upload(
        bucket="documents",
        key="prefix/aborted",
        upload_id="abort-id",
    )
    await adapter.delete_object(bucket="documents", key="prefix/object")
    await adapter.close()
    await adapter.close()

    assert completed.etag == '"completed"'
    assert completed.checksum_sha256_b64 == "combined-2"
    assert head.size_bytes == 11
    assert head.metadata == {"contract": "m1"}
    assert content == b"range-bytes"
    assert control.body.closed is True
    assert control.calls[1][1]["ChecksumMode"] == "ENABLED"
    assert control.calls[2][1]["Range"] == "bytes=10-20"
    assert control.closed == 1
    assert presign.closed == 1

    with pytest.raises(ObjectStoreProtocolError):
        await Boto3MultipartObjectStore(
            settings=ObjectStoreSettings(),
            control_client=RecordingS3Client(),
            presign_client=RecordingS3Client(),
        ).complete_upload(
            bucket="documents",
            key="prefix/object",
            upload_id="upload-id",
            parts=(parts[1],),
        )


async def test_adapter_maps_boto_errors_without_exposing_object_identifiers() -> None:
    control = RecordingS3Client()
    secret_key = "secret/key/path"
    control.error = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": secret_key}},
        "HeadObject",
    )
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingS3Client(),
    )

    with pytest.raises(ObjectStoreNotFound) as exc_info:
        await adapter.head_object(bucket="documents", key=secret_key)

    assert secret_key not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert secret_key not in "".join(traceback.format_exception(exc_info.value))


@pytest.mark.parametrize(
    ("content", "content_range"),
    [
        (b"short", "bytes 10-20/21"),
        (b"range-bytes", "bytes 0-10/21"),
    ],
)
async def test_adapter_rejects_short_or_mismatched_range_responses(
    content: bytes,
    content_range: str,
) -> None:
    control = RecordingS3Client()
    control.body = FakeBody(content)
    original_get_object = control.get_object

    def get_object(**kwargs: Any) -> dict[str, Any]:
        response = original_get_object(**kwargs)
        response["ContentRange"] = content_range
        return response

    control.get_object = get_object  # type: ignore[method-assign]
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingS3Client(),
    )

    with pytest.raises(ObjectStoreProtocolError):
        await adapter.get_range(
            bucket="documents",
            key="prefix/object",
            start=10,
            end_inclusive=20,
        )

    assert control.body.closed is True


async def test_adapter_maps_malformed_sdk_responses_to_protocol_errors() -> None:
    control = RecordingS3Client()
    control.list_part_pages = [
        {
            "IsTruncated": False,
            "Parts": [{"PartNumber": None, "Size": 1, "ETag": '"bad"'}],
        }
    ]
    adapter = Boto3MultipartObjectStore(
        settings=ObjectStoreSettings(),
        control_client=control,
        presign_client=RecordingS3Client(),
    )

    with pytest.raises(ObjectStoreProtocolError):
        await adapter.list_parts(
            bucket="documents",
            key="prefix/object",
            upload_id="upload-id",
        )


def test_client_factory_uses_sigv4_path_style_bounded_pool_and_separate_endpoints() -> None:
    settings = ObjectStoreSettings(
        endpoint="http://service-store.test:9000",
        presign_endpoint="http://browser-store.test:9000",
        connect_timeout_seconds=2,
        read_timeout_seconds=7,
        max_pool_connections=23,
    )
    control = create_s3_client(settings, endpoint_url=settings.endpoint)
    presign = create_s3_client(settings, endpoint_url=settings.presign_endpoint)
    try:
        assert control.meta.endpoint_url == "http://service-store.test:9000"
        assert presign.meta.endpoint_url == "http://browser-store.test:9000"
        assert control.meta.config.signature_version == "s3v4"
        assert control.meta.config.s3["addressing_style"] == "path"
        assert control.meta.config.connect_timeout == 2
        assert control.meta.config.read_timeout == 7
        assert control.meta.config.max_pool_connections == 23
    finally:
        control.close()
        presign.close()
