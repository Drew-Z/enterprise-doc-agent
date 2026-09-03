from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import Field, StrictBool
from starlette.responses import StreamingResponse

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.agents import (
    AgentDocumentVersionNotReady,
    AgentPrincipalForbidden,
    AgentRunAttemptResult,
    AgentRunError,
    AgentRunEventResult,
    AgentRunExecutionResult,
    AgentRunIdempotencyConflict,
    AgentRunInputInvalid,
    AgentRunIntegrityError,
    AgentRunNotFound,
    AgentRunStatusResult,
    AgentRunTaskType,
    AgentSseCursorInvalid,
    CreateAgentRunInput,
    CreateAgentRunResult,
    ReadyDocumentVersionResult,
    agent_sse_heartbeat,
    encode_agent_sse_event,
    is_terminal_agent_event,
    parse_last_event_id,
)
from enterprise_doc_core.context import PrincipalContext, get_request_context


class AgentRunServiceProtocol(Protocol):
    async def create(
        self,
        *,
        principal: PrincipalContext,
        idempotency_key: str,
        request: CreateAgentRunInput,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> CreateAgentRunResult: ...

    async def get_status(
        self, *, run_id: UUID, tenant_id: UUID, actor_id: UUID
    ) -> AgentRunStatusResult: ...

    async def list_events(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        after_seq: int = 0,
        limit: int = 100,
    ) -> tuple[AgentRunEventResult, ...]: ...

    async def cancel(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> AgentRunStatusResult: ...

    async def list_ready_document_versions(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> tuple[ReadyDocumentVersionResult, ...]: ...


class DisconnectProbe(Protocol):
    async def is_disconnected(self) -> bool: ...


_TERMINAL_RUN_STATUSES = frozenset(
    {"cancelled", "expired", "failed", "refused", "rejected", "succeeded"}
)
_SSE_BATCH_SIZE = 200
_SSE_INITIAL_POLL_SECONDS = 0.1
_SSE_MAX_POLL_SECONDS = 2.0
_SSE_HEARTBEAT_SECONDS = 15.0


class AgentRunCreateRequest(ApiModel):
    document_version_id: UUID
    task_type: AgentRunTaskType
    input_text: str = Field(min_length=1, max_length=20_000)
    extraction_schema: dict[str, Any] | None = None
    publish_requested: StrictBool = False


class AgentRunCreateResponse(ApiModel):
    run_id: UUID
    job_id: UUID
    status: str
    replayed: bool
    created_at: datetime


class AgentRunAttemptResponse(ApiModel):
    attempt_id: UUID
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    diagnostic_code: str | None = None


class AgentRunExecutionResponse(ApiModel):
    execution_id: UUID
    sequence: int
    kind: str
    job_id: UUID
    job_status: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    attempt_history: list[AgentRunAttemptResponse]


class AgentRunStatusResponse(ApiModel):
    run_id: UUID
    tenant_id: UUID
    document_version_id: UUID
    task_type: str
    publish_requested: bool
    status: str
    graph_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    model_version: str | None
    model_revision: str | None
    fallback_trigger_code: Annotated[
        str | None,
        Field(pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    ]
    provider_request_count: int | None
    provider_usage_request_count: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    repair_request_count: int | None
    fallback_count: int | None
    breaker_state: str | None
    tool_schema_version: str
    current_execution_seq: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    waiting_at: datetime | None
    finished_at: datetime | None
    cancelled_at: datetime | None
    executions: list[AgentRunExecutionResponse]


class AgentRunEventResponse(ApiModel):
    event_id: UUID
    seq: int
    event_type: str
    event_version: int
    public_payload: dict[str, Any]
    created_at: datetime


class ReadyDocumentVersionResponse(ApiModel):
    version_id: UUID
    document_id: UUID
    generation_id: UUID
    filename: str
    size_bytes: int
    content_sha256: str
    created_at: datetime


router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])


@router.post(
    "",
    response_model=AgentRunCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": AgentRunCreateResponse, "description": "Idempotent replay."},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def create_agent_run(
    payload: AgentRunCreateRequest,
    response: Response,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentRunCreateResponse:
    if idempotency_key is None:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="idempotency_key_required",
            message="An Idempotency-Key header is required.",
        )
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    context = get_request_context()
    try:
        result = await service.create(
            principal=principal,
            idempotency_key=idempotency_key,
            request=CreateAgentRunInput(
                document_version_id=payload.document_version_id,
                task_type=payload.task_type,
                input_text=payload.input_text,
                extraction_schema=payload.extraction_schema,
                publish_requested=payload.publish_requested,
            ),
            request_id=context.request_id if context is not None else None,
            correlation_id=context.correlation_id if context is not None else None,
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return AgentRunCreateResponse(
        run_id=result.run_id,
        job_id=result.job_id,
        status=result.status,
        replayed=result.replayed,
        created_at=result.created_at,
    )


@router.get(
    "/ready-document-versions",
    response_model=list[ReadyDocumentVersionResponse],
    responses={401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def list_ready_document_versions(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[ReadyDocumentVersionResponse]:
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    versions = await service.list_ready_document_versions(
        tenant_id=UUID(principal.tenant_id), actor_id=UUID(principal.actor_id)
    )
    return [_ready_document_version_response(version) for version in versions]


@router.get(
    "/{run_id}",
    response_model=AgentRunStatusResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_agent_run(
    run_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AgentRunStatusResponse:
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    try:
        result = await service.get_status(
            run_id=run_id,
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    return _status_response(result)


@router.get(
    "/{run_id}/events",
    response_model=list[AgentRunEventResponse],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_agent_run_events(
    run_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    after_seq: Annotated[int, Query(alias="afterSeq", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AgentRunEventResponse]:
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    try:
        events = await service.list_events(
            run_id=run_id,
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            after_seq=after_seq,
            limit=limit,
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    return [_event_response(event) for event in events]


@router.get(
    "/{run_id}/events/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Ordered Agent run events with resumable sequence IDs.",
        },
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def stream_agent_run_events(
    run_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        cursor = parse_last_event_id(last_event_id)
    except AgentSseCursorInvalid as error:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="agent_event_cursor_invalid",
            message="Last-Event-ID must be a non-negative integer sequence.",
        ) from error
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    tenant_id = UUID(principal.tenant_id)
    actor_id = UUID(principal.actor_id)
    try:
        initial_status = await service.get_status(
            run_id=run_id, tenant_id=tenant_id, actor_id=actor_id
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    return StreamingResponse(
        _stream_agent_run_events(
            service=service,
            request=request,
            run_id=run_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            after_seq=cursor,
            initial_status=initial_status.status,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{run_id}/cancel",
    response_model=AgentRunStatusResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def cancel_agent_run(
    run_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AgentRunStatusResponse:
    service = cast(AgentRunServiceProtocol, request.app.state.agent_run_service)
    try:
        result = await service.cancel(
            run_id=run_id,
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    return _status_response(result)


async def _stream_agent_run_events(
    *,
    service: AgentRunServiceProtocol,
    request: DisconnectProbe,
    run_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    after_seq: int,
    initial_status: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    initial_poll_seconds: float = _SSE_INITIAL_POLL_SECONDS,
    max_poll_seconds: float = _SSE_MAX_POLL_SECONDS,
    heartbeat_seconds: float = _SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    cursor = after_seq
    poll_seconds = initial_poll_seconds
    next_heartbeat = monotonic() + heartbeat_seconds
    current_status = initial_status
    while True:
        if await request.is_disconnected():
            return
        events = await service.list_events(
            run_id=run_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            after_seq=cursor,
            limit=_SSE_BATCH_SIZE,
        )
        if events:
            poll_seconds = initial_poll_seconds
            for event in events:
                if event.seq <= cursor:
                    continue
                yield encode_agent_sse_event(event)
                cursor = event.seq
                if is_terminal_agent_event(event):
                    return
            continue

        if current_status in _TERMINAL_RUN_STATUSES:
            return
        status_result = await service.get_status(
            run_id=run_id, tenant_id=tenant_id, actor_id=actor_id
        )
        current_status = status_result.status
        if current_status in _TERMINAL_RUN_STATUSES:
            return
        now = monotonic()
        if now >= next_heartbeat:
            yield agent_sse_heartbeat()
            next_heartbeat = now + heartbeat_seconds
        await sleep(poll_seconds)
        poll_seconds = min(max_poll_seconds, poll_seconds * 2)


def _attempt_response(attempt: AgentRunAttemptResult) -> AgentRunAttemptResponse:
    return AgentRunAttemptResponse(
        attempt_id=attempt.attempt_id,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        worker_id=attempt.worker_id,
        started_at=attempt.started_at,
        heartbeat_at=attempt.heartbeat_at,
        finished_at=attempt.finished_at,
        error_code=attempt.error_code,
        diagnostic_code=attempt.diagnostic_code,
    )


def _event_response(event: AgentRunEventResult) -> AgentRunEventResponse:
    return AgentRunEventResponse(
        event_id=event.event_id,
        seq=event.seq,
        event_type=event.event_type,
        event_version=event.event_version,
        public_payload=event.public_payload,
        created_at=event.created_at,
    )


def _ready_document_version_response(
    version: ReadyDocumentVersionResult,
) -> ReadyDocumentVersionResponse:
    return ReadyDocumentVersionResponse(
        version_id=version.version_id,
        document_id=version.document_id,
        generation_id=version.generation_id,
        filename=version.filename,
        size_bytes=version.size_bytes,
        content_sha256=version.content_sha256,
        created_at=version.created_at,
    )


def _execution_response(execution: AgentRunExecutionResult) -> AgentRunExecutionResponse:
    return AgentRunExecutionResponse(
        execution_id=execution.execution_id,
        sequence=execution.sequence,
        kind=execution.kind,
        job_id=execution.job_id,
        job_status=execution.job_status,
        attempts=execution.attempts,
        max_attempts=execution.max_attempts,
        cancel_requested=execution.cancel_requested,
        attempt_history=[_attempt_response(attempt) for attempt in execution.attempt_history],
    )


def _status_response(result: AgentRunStatusResult) -> AgentRunStatusResponse:
    return AgentRunStatusResponse(
        run_id=result.run_id,
        tenant_id=result.tenant_id,
        document_version_id=result.document_version_id,
        task_type=result.task_type,
        publish_requested=result.publish_requested,
        status=result.status,
        graph_version=result.graph_version,
        prompt_version=result.prompt_version,
        model_provider=result.model_provider,
        model_name=result.model_name,
        model_version=result.model_version,
        model_revision=result.model_revision,
        fallback_trigger_code=result.fallback_trigger_code,
        provider_request_count=result.provider_request_count,
        provider_usage_request_count=result.provider_usage_request_count,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        repair_request_count=result.repair_request_count,
        fallback_count=result.fallback_count,
        breaker_state=result.breaker_state,
        tool_schema_version=result.tool_schema_version,
        current_execution_seq=result.current_execution_seq,
        error_code=result.error_code,
        created_at=result.created_at,
        started_at=result.started_at,
        waiting_at=result.waiting_at,
        finished_at=result.finished_at,
        cancelled_at=result.cancelled_at,
        executions=[_execution_response(execution) for execution in result.executions],
    )


def _agent_run_api_error(error: AgentRunError) -> ApiError:
    if isinstance(error, AgentRunIdempotencyConflict):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, (AgentRunNotFound, AgentDocumentVersionNotReady)):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, AgentPrincipalForbidden):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, AgentRunInputInvalid):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, AgentRunIntegrityError):
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ApiError(status_code=status_code, code=error.code, message=error.message)
