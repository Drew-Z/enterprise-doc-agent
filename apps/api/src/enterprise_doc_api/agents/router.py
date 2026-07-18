from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import Field, StrictBool

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
    CreateAgentRunInput,
    CreateAgentRunResult,
    ReadyDocumentVersionResult,
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

    async def get_status(self, *, run_id: UUID, tenant_id: UUID) -> AgentRunStatusResult: ...

    async def list_events(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
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
    ) -> tuple[ReadyDocumentVersionResult, ...]: ...


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
    versions = await service.list_ready_document_versions(tenant_id=UUID(principal.tenant_id))
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
        result = await service.get_status(run_id=run_id, tenant_id=UUID(principal.tenant_id))
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
            after_seq=after_seq,
            limit=limit,
        )
    except AgentRunError as error:
        raise _agent_run_api_error(error) from error
    return [_event_response(event) for event in events]


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
