from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from typing import Any, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError

from enterprise_doc_core.config import ObjectStoreChecksumMode, ObjectStoreSettings
from enterprise_doc_core.object_store.client import create_s3_client
from enterprise_doc_core.object_store.errors import (
    ObjectStoreChecksumMismatch,
    ObjectStoreError,
    ObjectStoreProtocolError,
    normalize_object_store_error,
)
from enterprise_doc_core.object_store.metrics import instrument_object_store_operation
from enterprise_doc_core.object_store.models import (
    ArtifactObject,
    ObjectHead,
    PresignedObjectDownload,
)
from enterprise_doc_core.telemetry import MetricsRuntime


class _ReadableBody(Protocol):
    def read(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class ArtifactObjectStore(Protocol):
    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ArtifactObject: ...

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead: ...

    async def delete_object(self, *, bucket: str, key: str) -> None: ...

    async def presign_get(
        self,
        *,
        bucket: str,
        key: str,
        expires_in_seconds: int,
    ) -> PresignedObjectDownload: ...

    async def close(self) -> None: ...


class Boto3ArtifactObjectStore:
    """Bounded artifact object operations with post-write integrity verification."""

    def __init__(
        self,
        *,
        settings: ObjectStoreSettings,
        max_object_bytes: int = 16 * 1024 * 1024,
        control_client: Any | None = None,
        presign_client: Any | None = None,
        metrics: MetricsRuntime | None = None,
    ) -> None:
        if not 1 <= max_object_bytes <= 256 * 1024 * 1024:
            raise ValueError("max_object_bytes must be between 1 and 256 MiB")
        self.settings = settings
        self.max_object_bytes = max_object_bytes
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
        self.metrics = metrics

    @instrument_object_store_operation("write")
    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ArtifactObject:
        _validate_location(bucket=bucket, key=key)
        _validate_body(body, max_bytes=self.max_object_bytes)
        _validate_content_type(content_type)
        normalized_metadata = _validate_metadata(metadata or {})
        content_sha256 = hashlib.sha256(body).hexdigest()
        checksum_b64 = base64.b64encode(bytes.fromhex(content_sha256)).decode("ascii")
        if "sha256" in normalized_metadata:
            raise ObjectStoreProtocolError()
        normalized_metadata = {"sha256": content_sha256, **normalized_metadata}
        parameters: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "Metadata": normalized_metadata,
        }
        if self.settings.multipart_checksum_mode is ObjectStoreChecksumMode.NATIVE_SHA256:
            parameters["ChecksumSHA256"] = checksum_b64
        await self._call(self.control_client.put_object, **parameters)
        head = await self._head_object(bucket=bucket, key=key)
        if head.size_bytes != len(body):
            raise ObjectStoreChecksumMismatch()
        metadata_sha256 = head.metadata.get("sha256")
        if metadata_sha256 != content_sha256:
            raise ObjectStoreChecksumMismatch()
        if head.checksum_sha256_b64 is not None and head.checksum_sha256_b64 != checksum_b64:
            raise ObjectStoreChecksumMismatch()
        if self.settings.multipart_checksum_mode is ObjectStoreChecksumMode.READBACK_SHA256:
            actual_body = await self._read_object_body(
                bucket=bucket,
                key=key,
                expected_size=len(body),
            )
            if hashlib.sha256(actual_body).hexdigest() != content_sha256:
                raise ObjectStoreChecksumMismatch()
        return ArtifactObject(
            bucket=bucket,
            key=key,
            size_bytes=head.size_bytes,
            content_sha256=content_sha256,
            content_type=head.content_type or content_type,
            etag=head.etag,
        )

    @instrument_object_store_operation("read")
    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        return await self._head_object(bucket=bucket, key=key)

    async def _head_object(self, *, bucket: str, key: str) -> ObjectHead:
        _validate_location(bucket=bucket, key=key)
        parameters: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if self.settings.multipart_checksum_mode is ObjectStoreChecksumMode.NATIVE_SHA256:
            parameters["ChecksumMode"] = "ENABLED"
        response = _require_mapping(
            await self._call(
                self.control_client.head_object,
                **parameters,
            )
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
            size_bytes=_require_int(response, "ContentLength"),
            etag=_require_str(response, "ETag"),
            checksum_sha256_b64=checksum,
            content_type=content_type,
            metadata={str(name): str(value) for name, value in metadata.items()},
        )

    async def _read_object_body(self, *, bucket: str, key: str, expected_size: int) -> bytes:
        def read_body() -> bytes:
            response = _require_mapping(self.control_client.get_object(Bucket=bucket, Key=key))
            raw_body = response.get("Body")
            if not callable(getattr(raw_body, "read", None)) or not callable(
                getattr(raw_body, "close", None)
            ):
                raise ObjectStoreProtocolError()
            body = cast(_ReadableBody, raw_body)
            try:
                if _require_int(response, "ContentLength") != expected_size:
                    raise ObjectStoreChecksumMismatch()
                content = body.read(expected_size + 1)
            finally:
                body.close()
            if not isinstance(content, bytes) or len(content) != expected_size:
                raise ObjectStoreChecksumMismatch()
            return content

        return cast(bytes, await self._call(read_body))

    @instrument_object_store_operation("write")
    async def delete_object(self, *, bucket: str, key: str) -> None:
        _validate_location(bucket=bucket, key=key)
        await self._call(self.control_client.delete_object, Bucket=bucket, Key=key)

    @instrument_object_store_operation("download")
    async def presign_get(
        self,
        *,
        bucket: str,
        key: str,
        expires_in_seconds: int,
    ) -> PresignedObjectDownload:
        _validate_location(bucket=bucket, key=key)
        if (
            isinstance(expires_in_seconds, bool)
            or not isinstance(expires_in_seconds, int)
            or expires_in_seconds < 1
        ):
            raise ObjectStoreProtocolError()
        effective_ttl = min(expires_in_seconds, self.settings.presign_ttl_seconds)
        url = await self._call(
            self.presign_client.generate_presigned_url,
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=effective_ttl,
            HttpMethod="GET",
        )
        if not isinstance(url, str) or not url:
            raise ObjectStoreProtocolError()
        return PresignedObjectDownload(url=url, expires_in_seconds=effective_ttl)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closed: set[int] = set()
        for client in (self.control_client, self.presign_client):
            if id(client) in closed:
                continue
            closed.add(id(client))
            await asyncio.to_thread(client.close)

    async def _call(self, operation: Any, **parameters: Any) -> Any:
        try:
            return await asyncio.to_thread(operation, **parameters)
        except (BotoCoreError, ClientError) as error:
            normalized_error: ObjectStoreError = normalize_object_store_error(error)
            raise normalized_error from None


def _validate_location(*, bucket: str, key: str) -> None:
    if not isinstance(bucket, str) or not 1 <= len(bucket) <= 255:
        raise ObjectStoreProtocolError()
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 512
        or key.startswith("/")
        or "\\" in key
        or "//" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ObjectStoreProtocolError()


def _validate_body(body: bytes, *, max_bytes: int) -> None:
    if not isinstance(body, bytes) or len(body) > max_bytes:
        raise ObjectStoreProtocolError()


def _validate_content_type(content_type: str) -> None:
    if not isinstance(content_type, str) or not 1 <= len(content_type) <= 128:
        raise ObjectStoreProtocolError()
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in content_type):
        raise ObjectStoreProtocolError()


def _validate_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(metadata, Mapping) or len(metadata) > 16:
        raise ObjectStoreProtocolError()
    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 64
            or not re.fullmatch(r"[a-z0-9-]+", key)
            or not isinstance(value, str)
            or len(value) > 256
        ):
            raise ObjectStoreProtocolError()
        normalized[key] = value
    return normalized


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObjectStoreProtocolError()
    return value


def _require_str(response: Mapping[str, Any], field: str) -> str:
    value = response.get(field)
    if not isinstance(value, str) or not value:
        raise ObjectStoreProtocolError()
    return value


def _require_int(response: Mapping[str, Any], field: str) -> int:
    value = response.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ObjectStoreProtocolError()
    return value


__all__ = ["ArtifactObjectStore", "Boto3ArtifactObjectStore"]
