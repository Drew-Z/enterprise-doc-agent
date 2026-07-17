from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store.client import create_s3_client
from enterprise_doc_core.object_store.errors import (
    ObjectStoreError,
    ObjectStoreProtocolError,
    normalize_object_store_error,
)
from enterprise_doc_core.object_store.models import (
    CompletedMultipartUpload,
    IncompleteUpload,
    ObjectHead,
    PresignedUploadPart,
    UploadedPart,
)


class MultipartObjectStore(Protocol):
    async def create_upload(
        self,
        *,
        bucket: str,
        key: str,
        metadata: Mapping[str, str],
    ) -> str: ...

    async def presign_upload_part(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        part_number: int,
        checksum_sha256_b64: str,
        expires_in_seconds: int,
    ) -> PresignedUploadPart: ...

    async def list_parts(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> tuple[UploadedPart, ...]: ...

    async def complete_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        parts: Sequence[UploadedPart],
    ) -> CompletedMultipartUpload: ...

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead: ...

    async def get_range(
        self,
        *,
        bucket: str,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> bytes: ...

    async def abort_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None: ...

    async def delete_object(self, *, bucket: str, key: str) -> None: ...

    async def list_incomplete_uploads(
        self,
        *,
        bucket: str,
        prefix: str,
    ) -> tuple[IncompleteUpload, ...]: ...

    async def close(self) -> None: ...


class _ReadableBody(Protocol):
    def read(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class Boto3MultipartObjectStore:
    def __init__(
        self,
        *,
        settings: ObjectStoreSettings,
        control_client: Any | None = None,
        presign_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.control_client = (
            control_client
            if control_client is not None
            else create_s3_client(settings, endpoint_url=settings.endpoint)
        )
        self.presign_client = (
            presign_client
            if presign_client is not None
            else create_s3_client(settings, endpoint_url=settings.presign_endpoint)
        )
        self._closed = False

    async def create_upload(
        self,
        *,
        bucket: str,
        key: str,
        metadata: Mapping[str, str],
    ) -> str:
        response = cast(
            dict[str, Any],
            await self._call(
                self.control_client.create_multipart_upload,
                Bucket=bucket,
                Key=key,
                ChecksumAlgorithm="SHA256",
                Metadata=dict(metadata),
            ),
        )
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise ObjectStoreProtocolError()
        return upload_id

    async def presign_upload_part(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        part_number: int,
        checksum_sha256_b64: str,
        expires_in_seconds: int,
    ) -> PresignedUploadPart:
        _validate_part_number(part_number)
        _validate_sha256_b64(checksum_sha256_b64)
        _validate_presign_ttl(expires_in_seconds)
        effective_ttl = min(expires_in_seconds, self.settings.presign_ttl_seconds)
        url = await self._call(
            self.presign_client.generate_presigned_url,
            ClientMethod="upload_part",
            Params={
                "Bucket": bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
                "ChecksumSHA256": checksum_sha256_b64,
            },
            ExpiresIn=effective_ttl,
            HttpMethod="PUT",
        )
        if not isinstance(url, str) or not url:
            raise ObjectStoreProtocolError()
        return PresignedUploadPart(
            url=url,
            headers={"x-amz-checksum-sha256": checksum_sha256_b64},
            expires_in_seconds=effective_ttl,
        )

    async def list_parts(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> tuple[UploadedPart, ...]:
        marker: int | None = None
        parts: list[UploadedPart] = []
        while True:
            parameters: dict[str, Any] = {
                "Bucket": bucket,
                "Key": key,
                "UploadId": upload_id,
                "MaxParts": 1000,
            }
            if marker is not None:
                parameters["PartNumberMarker"] = marker
            response = _require_mapping(
                await self._call(self.control_client.list_parts, **parameters)
            )
            for item in _require_items(response, "Parts"):
                parts.append(
                    UploadedPart(
                        part_number=_require_int(item, "PartNumber", minimum=1),
                        size_bytes=_require_int(item, "Size", minimum=0),
                        etag=_require_str(item, "ETag"),
                        checksum_sha256_b64=_require_str(item, "ChecksumSHA256"),
                    )
                )
            if not _require_truncation_flag(response):
                break
            next_marker = response.get("NextPartNumberMarker")
            if not isinstance(next_marker, int) or (marker is not None and next_marker <= marker):
                raise ObjectStoreProtocolError()
            marker = next_marker
        return tuple(sorted(parts, key=lambda part: part.part_number))

    async def complete_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        parts: Sequence[UploadedPart],
    ) -> CompletedMultipartUpload:
        expected_numbers = list(range(1, len(parts) + 1))
        if not parts or [part.part_number for part in parts] != expected_numbers:
            raise ObjectStoreProtocolError()
        response = _require_mapping(
            await self._call(
                self.control_client.complete_multipart_upload,
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {
                            "PartNumber": part.part_number,
                            "ETag": part.etag,
                            "ChecksumSHA256": part.checksum_sha256_b64,
                        }
                        for part in parts
                    ]
                },
            ),
        )
        etag = _require_str(response, "ETag")
        checksum = response.get("ChecksumSHA256")
        if checksum is not None and not isinstance(checksum, str):
            raise ObjectStoreProtocolError()
        return CompletedMultipartUpload(
            etag=etag,
            checksum_sha256_b64=checksum,
        )

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        response = _require_mapping(
            await self._call(
                self.control_client.head_object,
                Bucket=bucket,
                Key=key,
                ChecksumMode="ENABLED",
            ),
        )
        checksum = response.get("ChecksumSHA256")
        content_type = response.get("ContentType")
        metadata = response.get("Metadata", {})
        if checksum is not None and not isinstance(checksum, str):
            raise ObjectStoreProtocolError()
        if content_type is not None and not isinstance(content_type, str):
            raise ObjectStoreProtocolError()
        if not isinstance(metadata, Mapping):
            raise ObjectStoreProtocolError()
        return ObjectHead(
            size_bytes=_require_int(response, "ContentLength", minimum=0),
            etag=_require_str(response, "ETag"),
            checksum_sha256_b64=checksum,
            content_type=content_type,
            metadata={str(name): str(value) for name, value in metadata.items()},
        )

    async def get_range(
        self,
        *,
        bucket: str,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        if start < 0 or end_inclusive < start:
            raise ObjectStoreProtocolError()
        expected_length = end_inclusive - start + 1

        def read_range() -> bytes:
            response = _require_mapping(
                self.control_client.get_object(
                    Bucket=bucket,
                    Key=key,
                    Range=f"bytes={start}-{end_inclusive}",
                )
            )
            raw_body = response.get("Body")
            if not callable(getattr(raw_body, "read", None)) or not callable(
                getattr(raw_body, "close", None)
            ):
                raise ObjectStoreProtocolError()
            body = cast(_ReadableBody, raw_body)
            try:
                if _require_int(response, "ContentLength", minimum=0) != expected_length:
                    raise ObjectStoreProtocolError()
                if not _content_range_matches(
                    _require_str(response, "ContentRange"),
                    start=start,
                    end_inclusive=end_inclusive,
                ):
                    raise ObjectStoreProtocolError()
                content = body.read(expected_length + 1)
            finally:
                body.close()
            if not isinstance(content, bytes) or len(content) != expected_length:
                raise ObjectStoreProtocolError()
            return content

        return cast(bytes, await self._call(read_range))

    async def abort_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        await self._call(
            self.control_client.abort_multipart_upload,
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
        )

    async def delete_object(self, *, bucket: str, key: str) -> None:
        await self._call(self.control_client.delete_object, Bucket=bucket, Key=key)

    async def list_incomplete_uploads(
        self,
        *,
        bucket: str,
        prefix: str,
    ) -> tuple[IncompleteUpload, ...]:
        uploads = await self._list_incomplete_upload_pages(
            bucket=bucket,
            prefix=prefix,
        )
        if prefix and not uploads:
            uploads = await self._list_incomplete_upload_pages(
                bucket=bucket,
                prefix=None,
            )
        return tuple(upload for upload in uploads if upload.key.startswith(prefix))

    async def _list_incomplete_upload_pages(
        self,
        *,
        bucket: str,
        prefix: str | None,
    ) -> list[IncompleteUpload]:
        key_marker: str | None = None
        upload_id_marker: str | None = None
        uploads: list[IncompleteUpload] = []
        while True:
            parameters: dict[str, Any] = {
                "Bucket": bucket,
                "MaxUploads": 1000,
            }
            if prefix:
                parameters["Prefix"] = prefix
            if key_marker is not None and upload_id_marker is not None:
                parameters["KeyMarker"] = key_marker
                parameters["UploadIdMarker"] = upload_id_marker
            response = _require_mapping(
                await self._call(
                    self.control_client.list_multipart_uploads,
                    **parameters,
                ),
            )
            for item in _require_items(response, "Uploads"):
                initiated = item.get("Initiated")
                uploads.append(
                    IncompleteUpload(
                        key=_require_str(item, "Key"),
                        upload_id=_require_str(item, "UploadId"),
                        initiated_at=initiated if isinstance(initiated, datetime) else None,
                    )
                )
            if not _require_truncation_flag(response):
                break
            next_key_marker = response.get("NextKeyMarker")
            next_upload_id_marker = response.get("NextUploadIdMarker")
            if (
                not isinstance(next_key_marker, str)
                or not next_key_marker
                or not isinstance(next_upload_id_marker, str)
                or not next_upload_id_marker
                or (next_key_marker, next_upload_id_marker) == (key_marker, upload_id_marker)
            ):
                raise ObjectStoreProtocolError()
            key_marker = next_key_marker
            upload_id_marker = next_upload_id_marker
        return uploads

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closed: set[int] = set()
        for client in (self.control_client, self.presign_client):
            identity = id(client)
            if identity in closed:
                continue
            closed.add(identity)
            await asyncio.to_thread(client.close)

    async def _call(self, operation: Any, **parameters: Any) -> Any:
        try:
            return await asyncio.to_thread(operation, **parameters)
        except (BotoCoreError, ClientError) as error:
            normalized_error: ObjectStoreError = normalize_object_store_error(error)
        raise normalized_error


def _validate_part_number(part_number: int) -> None:
    if not 1 <= part_number <= 10_000:
        raise ObjectStoreProtocolError()


def _validate_presign_ttl(expires_in_seconds: object) -> None:
    if (
        isinstance(expires_in_seconds, bool)
        or not isinstance(expires_in_seconds, int)
        or expires_in_seconds < 1
    ):
        raise ObjectStoreProtocolError()


def _validate_sha256_b64(checksum: str) -> None:
    try:
        decoded = base64.b64decode(checksum, validate=True)
    except ValueError as error:
        raise ObjectStoreProtocolError() from error
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != checksum:
        raise ObjectStoreProtocolError()


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObjectStoreProtocolError()
    return value


def _require_items(
    response: Mapping[str, Any],
    field: str,
) -> tuple[Mapping[str, Any], ...]:
    value = response.get(field, [])
    if not isinstance(value, list):
        raise ObjectStoreProtocolError()
    return tuple(_require_mapping(item) for item in value)


def _require_str(response: Mapping[str, Any], field: str) -> str:
    value = response.get(field)
    if not isinstance(value, str) or not value:
        raise ObjectStoreProtocolError()
    return value


def _require_int(
    response: Mapping[str, Any],
    field: str,
    *,
    minimum: int,
) -> int:
    value = response.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ObjectStoreProtocolError()
    return value


def _require_truncation_flag(response: Mapping[str, Any]) -> bool:
    value = response.get("IsTruncated", False)
    if not isinstance(value, bool):
        raise ObjectStoreProtocolError()
    return value


_CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def _content_range_matches(
    value: str,
    *,
    start: int,
    end_inclusive: int,
) -> bool:
    match = _CONTENT_RANGE_PATTERN.fullmatch(value)
    if match is None:
        return False
    actual_start, actual_end, total_size = (int(part) for part in match.groups())
    return actual_start == start and actual_end == end_inclusive and total_size > end_inclusive
