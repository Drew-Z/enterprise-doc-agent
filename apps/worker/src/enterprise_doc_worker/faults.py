from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, SecretStr

from enterprise_doc_core.agents import (
    ChatModelGateway,
    CreateDraftArtifactInput,
    CreateDraftArtifactResult,
    GetArtifactInput,
    GetArtifactResult,
    GroundedModelOutput,
    GroundedModelRequest,
    PublishArtifactInput,
    PublishArtifactResult,
    ReadChunkInput,
    ReadChunkResult,
    SearchDocumentInput,
    SearchDocumentResult,
)
from enterprise_doc_core.agents.gateway import (
    ModelOutputSchemaError,
    ModelRateLimitedError,
    ModelServerError,
    ModelTimeoutError,
    ModelTransportError,
)
from enterprise_doc_core.config import FaultInjectionSettings
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_core.object_store import MultipartObjectStore
from enterprise_doc_core.object_store.errors import (
    ObjectStoreProtocolError,
    ObjectStoreUnavailable,
)
from enterprise_doc_core.object_store.models import (
    CompletedMultipartUpload,
    IncompleteUpload,
    ObjectHead,
    PresignedUploadPart,
    UploadedPart,
)
from enterprise_doc_worker.mcp_client import (
    McpClient,
    McpClientTimeout,
    McpClientTransportError,
    McpToolResultInvalid,
    McpToolReturnedError,
)
from enterprise_doc_worker.queue import AsyncJobHandler, JobHandlerError

_LOGGER = logging.getLogger("enterprise_doc_worker.faults")
_ResultT = TypeVar("_ResultT", bound=BaseModel)


class InjectedRetryableHandlerError(JobHandlerError):
    code = "fault_injected_retryable"
    message = "A local fault experiment injected a retryable handler failure."
    retryable = True


class InjectedPermanentHandlerError(JobHandlerError):
    code = "fault_injected_permanent"
    message = "A local fault experiment injected a permanent handler failure."


@dataclass(slots=True)
class FaultController:
    settings: FaultInjectionSettings
    _invocations: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    async def before(self, operation: str) -> bool:
        self._invocations[operation] += 1
        invocation = self._invocations[operation]
        if not self._should_trigger(invocation):
            return False
        if self.settings.delay_ms:
            await asyncio.sleep(self.settings.delay_ms / 1000)
        _LOGGER.warning(
            "fault_injected",
            extra={
                "event_data": {
                    "fault_target": self.settings.target,
                    "fault_mode": self.settings.mode,
                    "fault_operation": operation,
                    "fault_trigger_index": invocation,
                }
            },
        )
        return True

    def _should_trigger(self, invocation: int) -> bool:
        if not self.settings.enabled or invocation <= self.settings.trigger_after:
            return False
        relative = invocation - self.settings.trigger_after - 1
        if self.settings.trigger_every == 0:
            return relative == 0
        return relative % self.settings.trigger_every == 0


class FaultInjectingHandler:
    def __init__(self, inner: AsyncJobHandler, controller: FaultController) -> None:
        self.inner = inner
        self.controller = controller

    async def __call__(self, claim: ClaimedJob) -> None:
        if await self.controller.before(claim.job_type):
            if self.controller.settings.mode == "retryable":
                raise InjectedRetryableHandlerError()
            if self.controller.settings.mode == "permanent":
                raise InjectedPermanentHandlerError()
            if self.controller.settings.mode == "cancelled":
                raise asyncio.CancelledError
        await self.inner(claim)


class FaultInjectingModelGateway:
    def __init__(self, inner: ChatModelGateway, controller: FaultController) -> None:
        self.inner = inner
        self.controller = controller

    async def generate(self, request: GroundedModelRequest) -> GroundedModelOutput:
        if await self.controller.before("generate"):
            errors = {
                "model_timeout": ModelTimeoutError,
                "model_rate_limited": ModelRateLimitedError,
                "model_server_error": ModelServerError,
                "model_transport_error": ModelTransportError,
                "invalid_schema": ModelOutputSchemaError,
            }
            error_type = errors.get(self.controller.settings.mode)
            if error_type is not None:
                raise error_type()
        return await self.inner.generate(request)


class FaultInjectingMcpClient:
    def __init__(self, inner: McpClient, controller: FaultController) -> None:
        self.inner = inner
        self.controller = controller

    async def call(
        self,
        *,
        tool_name: str,
        request: BaseModel,
        result_model: type[_ResultT],
        context_token: SecretStr | str,
    ) -> _ResultT:
        if await self.controller.before(tool_name):
            errors = {
                "mcp_client_timeout": McpClientTimeout,
                "mcp_client_transport_error": McpClientTransportError,
                "mcp_tool_returned_error": McpToolReturnedError,
                "mcp_tool_result_invalid": McpToolResultInvalid,
            }
            error_type = errors.get(self.controller.settings.mode)
            if error_type is not None:
                raise error_type()
        return await self.inner.call(
            tool_name=tool_name,
            request=request,
            result_model=result_model,
            context_token=context_token,
        )

    async def search_document(
        self, *, context_token: SecretStr | str, request: SearchDocumentInput
    ) -> SearchDocumentResult:
        return await self.call(
            tool_name="search_document",
            request=request,
            result_model=SearchDocumentResult,
            context_token=context_token,
        )

    async def read_chunk(
        self, *, context_token: SecretStr | str, request: ReadChunkInput
    ) -> ReadChunkResult:
        return await self.call(
            tool_name="read_chunk",
            request=request,
            result_model=ReadChunkResult,
            context_token=context_token,
        )

    async def create_draft_artifact(
        self, *, context_token: SecretStr | str, request: CreateDraftArtifactInput
    ) -> CreateDraftArtifactResult:
        return await self.call(
            tool_name="create_draft_artifact",
            request=request,
            result_model=CreateDraftArtifactResult,
            context_token=context_token,
        )

    async def get_artifact(
        self, *, context_token: SecretStr | str, request: GetArtifactInput
    ) -> GetArtifactResult:
        return await self.call(
            tool_name="get_artifact",
            request=request,
            result_model=GetArtifactResult,
            context_token=context_token,
        )

    async def publish_artifact(
        self, *, context_token: SecretStr | str, request: PublishArtifactInput
    ) -> PublishArtifactResult:
        return await self.call(
            tool_name="publish_artifact",
            request=request,
            result_model=PublishArtifactResult,
            context_token=context_token,
        )


class FaultInjectingMultipartObjectStore:
    def __init__(self, inner: MultipartObjectStore, controller: FaultController) -> None:
        self.inner = inner
        self.controller = controller

    async def _before(self, operation: str) -> bool:
        triggered = await self.controller.before(operation)
        if not triggered:
            return False
        if self.controller.settings.mode == "object_store_unavailable":
            raise ObjectStoreUnavailable()
        if self.controller.settings.mode == "object_store_protocol_error":
            raise ObjectStoreProtocolError()
        return True

    async def create_upload(
        self,
        *,
        bucket: str,
        key: str,
        metadata: Mapping[str, str],
    ) -> str:
        await self._before("create_upload")
        return await self.inner.create_upload(bucket=bucket, key=key, metadata=metadata)

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
        await self._before("presign_upload_part")
        return await self.inner.presign_upload_part(
            bucket=bucket,
            key=key,
            upload_id=upload_id,
            part_number=part_number,
            checksum_sha256_b64=checksum_sha256_b64,
            expires_in_seconds=expires_in_seconds,
        )

    async def list_parts(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> tuple[UploadedPart, ...]:
        await self._before("list_parts")
        return await self.inner.list_parts(bucket=bucket, key=key, upload_id=upload_id)

    async def complete_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        parts: Sequence[UploadedPart],
    ) -> CompletedMultipartUpload:
        await self._before("complete_upload")
        return await self.inner.complete_upload(
            bucket=bucket,
            key=key,
            upload_id=upload_id,
            parts=parts,
        )

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        await self._before("head_object")
        return await self.inner.head_object(bucket=bucket, key=key)

    async def get_range(
        self,
        *,
        bucket: str,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        short_read = await self._before("get_range")
        body = await self.inner.get_range(
            bucket=bucket,
            key=key,
            start=start,
            end_inclusive=end_inclusive,
        )
        if short_read and self.controller.settings.mode == "short_read":
            return body[:-1]
        return body

    async def abort_upload(self, *, bucket: str, key: str, upload_id: str) -> None:
        await self._before("abort_upload")
        await self.inner.abort_upload(bucket=bucket, key=key, upload_id=upload_id)

    async def delete_object(self, *, bucket: str, key: str) -> None:
        await self._before("delete_object")
        await self.inner.delete_object(bucket=bucket, key=key)

    async def list_incomplete_uploads(
        self,
        *,
        bucket: str,
        prefix: str,
    ) -> tuple[IncompleteUpload, ...]:
        await self._before("list_incomplete_uploads")
        return await self.inner.list_incomplete_uploads(bucket=bucket, prefix=prefix)

    async def close(self) -> None:
        await self.inner.close()


def wrap_handler(
    handler: AsyncJobHandler,
    settings: FaultInjectionSettings,
) -> AsyncJobHandler:
    if settings.enabled and settings.target == "handler":
        return FaultInjectingHandler(handler, FaultController(settings))
    return handler


def wrap_model_gateway(
    gateway: ChatModelGateway,
    settings: FaultInjectionSettings,
) -> ChatModelGateway:
    if settings.enabled and settings.target == "model":
        return FaultInjectingModelGateway(gateway, FaultController(settings))
    return gateway


def wrap_mcp_client(client: McpClient, settings: FaultInjectionSettings) -> McpClient:
    if settings.enabled and settings.target == "mcp":
        return FaultInjectingMcpClient(client, FaultController(settings))
    return client


def wrap_multipart_store(
    store: MultipartObjectStore,
    settings: FaultInjectionSettings,
) -> MultipartObjectStore:
    if settings.enabled and settings.target == "multipart":
        return FaultInjectingMultipartObjectStore(store, FaultController(settings))
    return store


__all__ = [
    "FaultController",
    "FaultInjectingHandler",
    "FaultInjectingMcpClient",
    "FaultInjectingModelGateway",
    "FaultInjectingMultipartObjectStore",
    "InjectedPermanentHandlerError",
    "InjectedRetryableHandlerError",
    "wrap_handler",
    "wrap_mcp_client",
    "wrap_model_gateway",
    "wrap_multipart_store",
]
