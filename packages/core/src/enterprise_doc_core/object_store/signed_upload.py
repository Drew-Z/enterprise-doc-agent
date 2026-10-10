from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store.client import create_s3_client
from enterprise_doc_core.object_store.errors import (
    ObjectStoreProtocolError,
    normalize_object_store_error,
)
from enterprise_doc_core.object_store.metrics import instrument_object_store_operation
from enterprise_doc_core.object_store.models import ObjectHead
from enterprise_doc_core.telemetry import MetricsRuntime

MAX_SIGNED_UPLOAD_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SignedObjectWrite:
    """Acknowledgment of our own SHA256-signed PUT, not an existing-object proof."""

    head: ObjectHead
    content_sha256: str


class SignedUploadWriter(Protocol):
    async def write_content(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> SignedObjectWrite: ...


class Boto3SignedUploadWriter:
    """Bounded, create-only writes of bytes already received by a trusted server.

    Callers still own authorization, declared-hash/envelope validation and durable
    finalization. All failures require recovery; they are never write receipts.
    """

    def __init__(
        self, *, settings: ObjectStoreSettings, metrics: MetricsRuntime | None = None
    ) -> None:
        self._client = create_s3_client(settings, endpoint_url=settings.endpoint, sign_payload=True)
        self.metrics = metrics
        self._closed = False

    @instrument_object_store_operation("write")
    async def write_content(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> SignedObjectWrite:
        if (
            self._closed
            or type(body) is not bytes
            or not 0 < len(body) <= MAX_SIGNED_UPLOAD_BYTES
            or not bucket
            or not key
            or not content_type
        ):
            raise ObjectStoreProtocolError()
        owned_metadata = dict(metadata)
        content_sha256 = hashlib.sha256(body).hexdigest()
        try:
            response = await asyncio.to_thread(
                self._client.put_object,
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentLength=len(body),
                ContentType=content_type,
                Metadata=owned_metadata,
                IfNoneMatch="*",
            )
        except (BotoCoreError, ClientError) as error:
            normalized_error = normalize_object_store_error(error)
        else:
            if not isinstance(response, dict):
                raise ObjectStoreProtocolError()
            response_metadata = response.get("ResponseMetadata")
            etag = response.get("ETag")
            if (
                not isinstance(response_metadata, dict)
                or response_metadata.get("HTTPStatusCode") != 200
                or not isinstance(etag, str)
                or not etag.strip()
            ):
                raise ObjectStoreProtocolError()
            return SignedObjectWrite(
                head=ObjectHead(
                    size_bytes=len(body),
                    etag=etag,
                    checksum_sha256_b64=None,
                    content_type=content_type,
                    metadata=owned_metadata,
                ),
                content_sha256=content_sha256,
            )
        # Do not retain provider exception text/URLs in the public error chain.
        raise normalized_error

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await asyncio.to_thread(self._client.close)
