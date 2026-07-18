from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.jobs import (
    JobAttemptResult,
    JobEventResult,
    JobNotClaimable,
    JobNotFound,
    JobStatusResult,
)


class JobRuntimeServiceProtocol(Protocol):
    async def get_status(self, *, job_id: UUID, tenant_id: UUID) -> JobStatusResult: ...

    async def list_attempts(
        self, *, job_id: UUID, tenant_id: UUID
    ) -> tuple[JobAttemptResult, ...]: ...

    async def list_events(self, *, job_id: UUID, tenant_id: UUID) -> tuple[JobEventResult, ...]: ...

    async def retry_dead(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        actor_id: UUID | None = None,
    ) -> str: ...

    async def cancel(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        actor_id: UUID | None = None,
    ) -> str: ...


class JobAttemptResponse(ApiModel):
    attempt_id: UUID
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


class JobEventResponse(ApiModel):
    event_id: UUID
    seq: int
    event_type: str
    status: str | None
    payload: dict[str, object]
    created_at: datetime


class JobStatusResponse(ApiModel):
    job_id: UUID
    tenant_id: UUID
    document_version_id: UUID | None
    job_type: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    available_at: datetime
    last_error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool
    attempt_history: list[JobAttemptResponse]
    events: list[JobEventResponse]


class JobActionResponse(ApiModel):
    job_id: UUID
    status: str


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _service(request: Request) -> JobRuntimeServiceProtocol:
    service = cast(
        JobRuntimeServiceProtocol | None, getattr(request.app.state, "job_runtime_service", None)
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The job runtime is unavailable.",
        )
    return service


def _principal_ids(principal: PrincipalContext) -> tuple[UUID, UUID]:
    return UUID(principal.tenant_id), UUID(principal.actor_id)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> JobStatusResponse:
    service = _service(request)
    tenant_id, _ = _principal_ids(principal)
    try:
        result = await service.get_status(job_id=job_id, tenant_id=tenant_id)
        attempts = await service.list_attempts(job_id=job_id, tenant_id=tenant_id)
        events = await service.list_events(job_id=job_id, tenant_id=tenant_id)
    except JobNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        ) from error
    return JobStatusResponse(
        job_id=result.job_id,
        tenant_id=result.tenant_id,
        document_version_id=result.document_version_id,
        job_type=result.job_type,
        status=result.status,
        priority=result.priority,
        attempts=result.attempts,
        max_attempts=result.max_attempts,
        available_at=result.available_at,
        last_error_code=result.last_error_code,
        started_at=result.started_at,
        finished_at=result.finished_at,
        cancel_requested=result.cancel_requested,
        attempt_history=[
            JobAttemptResponse(
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                worker_id=attempt.worker_id,
                started_at=attempt.started_at,
                heartbeat_at=attempt.heartbeat_at,
                finished_at=attempt.finished_at,
                error_code=attempt.error_code,
            )
            for attempt in attempts
        ],
        events=[
            JobEventResponse(
                event_id=event.event_id,
                seq=event.seq,
                event_type=event.event_type,
                status=event.status,
                payload=event.payload,
                created_at=event.created_at,
            )
            for event in events
        ],
    )


@router.post("/{job_id}/retry", response_model=JobActionResponse)
async def retry_job(
    job_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> JobActionResponse:
    service = _service(request)
    tenant_id, actor_id = _principal_ids(principal)
    try:
        result = await service.retry_dead(
            job_id=job_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
    except JobNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        ) from error
    except JobNotClaimable as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Job is not dead."
        ) from error
    return JobActionResponse(job_id=job_id, status=result)


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(
    job_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> JobActionResponse:
    service = _service(request)
    tenant_id, actor_id = _principal_ids(principal)
    try:
        result = await service.cancel(
            job_id=job_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
    except JobNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        ) from error
    return JobActionResponse(job_id=job_id, status=result)
