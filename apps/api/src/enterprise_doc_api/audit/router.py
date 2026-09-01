from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import Field

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.audit import (
    AuditArchiveBatchResult,
    AuditArchiveDownloadResult,
    AuditArchiveUnavailable,
    AuditArchiveVerificationFailed,
    AuditArchiveVerificationResult,
    AuditEventPage,
    AuditEventResult,
    AuditGovernanceError,
    AuditGovernanceForbidden,
    AuditGovernanceInvalid,
    AuditGovernanceNotFound,
    AuditLegalHoldResult,
    AuditRetentionPlan,
    AuditRetentionPolicyResult,
    AuditRetentionPreview,
)
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.identity import MembershipRole


class AuditEventServiceProtocol(Protocol):
    async def list_events(
        self,
        *,
        tenant_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> AuditEventPage: ...


class AuditGovernanceServiceProtocol(Protocol):
    async def get_retention_policy(self, *, tenant_id: UUID) -> AuditRetentionPolicyResult: ...

    async def set_retention_policy(self, **kwargs: object) -> AuditRetentionPolicyResult: ...

    async def list_legal_holds(self, *, tenant_id: UUID) -> tuple[AuditLegalHoldResult, ...]: ...

    async def create_legal_hold(self, **kwargs: object) -> AuditLegalHoldResult: ...

    async def release_legal_hold(self, **kwargs: object) -> AuditLegalHoldResult: ...

    async def retention_preview(self, *, tenant_id: UUID) -> AuditRetentionPreview: ...

    async def retention_plan(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
    ) -> AuditRetentionPlan: ...

    async def archive_retention_plan(self, **kwargs: object) -> AuditArchiveBatchResult: ...

    async def list_archive_batches(
        self,
        *,
        tenant_id: UUID,
        limit: int = 25,
    ) -> tuple[AuditArchiveBatchResult, ...]: ...

    async def verify_archive_batch(self, **kwargs: object) -> AuditArchiveVerificationResult: ...

    async def get_archive_download(self, **kwargs: object) -> AuditArchiveDownloadResult: ...


class AuditEventResponse(ApiModel):
    event_id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID | None
    occurred_at: datetime
    request_id: str | None
    correlation_id: str | None
    metadata: dict[str, object] = Field(default_factory=dict)
    schema_version: int


class AuditEventPageResponse(ApiModel):
    items: list[AuditEventResponse]
    next_cursor: str | None


class AuditRetentionPolicyResponse(ApiModel):
    tenant_id: UUID
    retention_days: int
    is_enabled: bool
    updated_by: UUID | None


class AuditRetentionPolicyUpdateRequest(ApiModel):
    retention_days: int = Field(ge=30, le=3650)
    is_enabled: bool


class AuditLegalHoldResponse(ApiModel):
    hold_id: UUID
    tenant_id: UUID
    name: str
    reason: str
    resource_type: str | None
    resource_id: UUID | None
    starts_at: datetime
    expires_at: datetime | None
    released_at: datetime | None
    created_by: UUID | None
    released_by: UUID | None


class AuditLegalHoldCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    resource_type: str | None = Field(default=None, max_length=80)
    resource_id: UUID | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None


class AuditRetentionPreviewResponse(ApiModel):
    cutoff_at: datetime | None
    eligible_event_count: int
    protected_event_count: int


class AuditRetentionPlanResponse(ApiModel):
    policy: AuditRetentionPolicyResponse
    cutoff_at: datetime | None
    eligible_event_count: int
    protected_event_count: int
    eligible_event_ids: list[UUID]
    fingerprint: str


class AuditArchiveBatchResponse(ApiModel):
    batch_id: UUID
    tenant_id: UUID
    cutoff_at: datetime
    archived_event_count: int
    fingerprint: str
    bucket: str
    object_key: str
    content_sha256: str
    size_bytes: int
    created_by: UUID | None
    created_at: datetime | None = None


class AuditArchiveVerificationResponse(ApiModel):
    batch_id: UUID
    tenant_id: UUID
    verified_at: datetime
    valid: bool
    expected_sha256: str
    actual_sha256: str | None
    expected_size_bytes: int
    actual_size_bytes: int | None
    envelope_valid: bool
    failure_reason: str | None


class AuditArchiveDownloadResponse(ApiModel):
    batch_id: UUID
    tenant_id: UUID
    bucket: str
    object_key: str
    content_sha256: str
    size_bytes: int
    url: str
    expires_in_seconds: int


router = APIRouter(prefix="/api/audit-events", tags=["audit"])

governance_router = APIRouter(prefix="/api/audit-governance", tags=["audit"])


class AuditExportForbidden(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="audit_export_forbidden",
            message="Only a tenant owner can export audit events.",
        )


def _get_audit_service(request: Request) -> AuditEventServiceProtocol:
    service = cast(
        AuditEventServiceProtocol | None,
        getattr(request.app.state, "audit_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The audit log is unavailable.",
        )
    return service


def _get_governance_service(request: Request) -> AuditGovernanceServiceProtocol:
    service = cast(
        AuditGovernanceServiceProtocol | None,
        getattr(request.app.state, "audit_governance_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The audit governance service is unavailable.",
        )
    return service


def _validate_time_window(
    from_date: datetime | None,
    to_date: datetime | None,
) -> None:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(status_code=422, detail="from must be before to")


def _event_response(event: AuditEventResult) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        actor_id=event.actor_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        occurred_at=event.occurred_at,
        request_id=event.request_id,
        correlation_id=event.correlation_id,
        metadata=event.metadata,
        schema_version=event.schema_version,
    )


async def _list_export_events(
    service: AuditEventServiceProtocol,
    *,
    tenant_id: UUID,
    max_events: int,
    from_date: datetime | None,
    to_date: datetime | None,
    action: str | None,
    resource_type: str | None,
    resource_id: UUID | None,
    actor_id: UUID | None,
) -> list[AuditEventResult]:
    events: list[AuditEventResult] = []
    cursor: str | None = None
    while len(events) < max_events:
        page_size = min(200, max_events - len(events))
        page = await service.list_events(
            tenant_id=tenant_id,
            limit=page_size,
            cursor=cursor,
            from_date=from_date,
            to_date=to_date,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
        )
        events.extend(page.items)
        if page.next_cursor is None or not page.items:
            break
        cursor = page.next_cursor
    return events[:max_events]


@router.get(
    "",
    response_model=AuditEventPageResponse,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_audit_events(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    resource_type: Annotated[str | None, Query(alias="resourceType", max_length=80)] = None,
    resource_id: Annotated[UUID | None, Query(alias="resourceId")] = None,
    actor_id: Annotated[UUID | None, Query(alias="actorId")] = None,
) -> AuditEventPageResponse:
    service = _get_audit_service(request)
    _validate_time_window(from_date, to_date)
    try:
        page = await service.list_events(
            tenant_id=UUID(principal.tenant_id),
            limit=limit,
            cursor=cursor,
            from_date=from_date,
            to_date=to_date,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AuditEventPageResponse(
        items=[_event_response(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/export.csv",
    response_class=Response,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def export_audit_events(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 2000,
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    resource_type: Annotated[str | None, Query(alias="resourceType", max_length=80)] = None,
    resource_id: Annotated[UUID | None, Query(alias="resourceId")] = None,
    actor_id: Annotated[UUID | None, Query(alias="actorId")] = None,
) -> Response:
    if principal.role != MembershipRole.OWNER.value:
        raise AuditExportForbidden()
    service = _get_audit_service(request)
    _validate_time_window(from_date, to_date)
    try:
        events = await _list_export_events(
            service,
            tenant_id=UUID(principal.tenant_id),
            max_events=limit,
            from_date=from_date,
            to_date=to_date,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "event_id",
            "tenant_id",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "occurred_at",
            "request_id",
            "correlation_id",
            "metadata",
            "schema_version",
        ]
    )
    for item in events:
        event = _event_response(item)
        writer.writerow(
            [
                str(event.event_id),
                str(event.tenant_id),
                str(event.actor_id) if event.actor_id else "",
                event.action,
                event.resource_type,
                str(event.resource_id) if event.resource_id else "",
                event.occurred_at.isoformat(),
                event.request_id or "",
                event.correlation_id or "",
                json.dumps(event.metadata, ensure_ascii=True, sort_keys=True),
                event.schema_version,
            ]
        )
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )


@governance_router.get(
    "/retention-policy",
    response_model=AuditRetentionPolicyResponse,
    responses={401: {"model": ErrorResponse}},
)
async def get_retention_policy(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditRetentionPolicyResponse:
    _require_governance_owner(principal)
    result = await _get_governance_service(request).get_retention_policy(
        tenant_id=UUID(principal.tenant_id)
    )
    return AuditRetentionPolicyResponse.model_validate(result, from_attributes=True)


@governance_router.put(
    "/retention-policy",
    response_model=AuditRetentionPolicyResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_retention_policy(
    payload: AuditRetentionPolicyUpdateRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditRetentionPolicyResponse:
    context = get_request_context()
    try:
        result = await _get_governance_service(request).set_retention_policy(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            retention_days=payload.retention_days,
            is_enabled=payload.is_enabled,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditRetentionPolicyResponse.model_validate(result, from_attributes=True)


@governance_router.get(
    "/retention-preview",
    response_model=AuditRetentionPreviewResponse,
    responses={401: {"model": ErrorResponse}},
)
async def get_retention_preview(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditRetentionPreviewResponse:
    _require_governance_owner(principal)
    result = await _get_governance_service(request).retention_preview(
        tenant_id=UUID(principal.tenant_id)
    )
    return AuditRetentionPreviewResponse.model_validate(result, from_attributes=True)


@governance_router.get(
    "/retention-plan",
    response_model=AuditRetentionPlanResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def get_retention_plan(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditRetentionPlanResponse:
    _require_governance_owner(principal)
    try:
        result = await _get_governance_service(request).retention_plan(
            tenant_id=UUID(principal.tenant_id),
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AuditRetentionPlanResponse(
        policy=AuditRetentionPolicyResponse.model_validate(result.policy, from_attributes=True),
        cutoff_at=result.cutoff_at,
        eligible_event_count=result.eligible_event_count,
        protected_event_count=result.protected_event_count,
        eligible_event_ids=list(result.eligible_event_ids),
        fingerprint=result.fingerprint,
    )


@governance_router.post(
    "/retention-archive",
    response_model=AuditArchiveBatchResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def archive_retention_plan(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditArchiveBatchResponse:
    context = get_request_context()
    try:
        result = await _get_governance_service(request).archive_retention_plan(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            limit=limit,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditArchiveUnavailable as error:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=error.code,
            message=str(error) or "The audit archive store is unavailable.",
        ) from error
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditArchiveBatchResponse.model_validate(result, from_attributes=True)


@governance_router.get(
    "/retention-archives",
    response_model=list[AuditArchiveBatchResponse],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def list_retention_archives(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[AuditArchiveBatchResponse]:
    _require_governance_owner(principal)
    try:
        batches = await _get_governance_service(request).list_archive_batches(
            tenant_id=UUID(principal.tenant_id),
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [
        AuditArchiveBatchResponse.model_validate(batch, from_attributes=True) for batch in batches
    ]


@governance_router.post(
    "/retention-archives/{batch_id}/verify",
    response_model=AuditArchiveVerificationResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def verify_retention_archive(
    batch_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditArchiveVerificationResponse:
    context = get_request_context()
    try:
        result = await _get_governance_service(request).verify_archive_batch(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            batch_id=batch_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditArchiveUnavailable as error:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=error.code,
            message=str(error) or "The audit archive store is unavailable.",
        ) from error
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditArchiveVerificationResponse.model_validate(result, from_attributes=True)


@governance_router.get(
    "/retention-archives/{batch_id}/download",
    response_model=AuditArchiveDownloadResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_retention_archive_download(
    batch_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    expires_in_seconds: Annotated[int, Query(alias="expiresIn", ge=60, le=900)] = 300,
) -> AuditArchiveDownloadResponse:
    context = get_request_context()
    try:
        result = await _get_governance_service(request).get_archive_download(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            batch_id=batch_id,
            expires_in_seconds=expires_in_seconds,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditArchiveUnavailable as error:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=error.code,
            message=str(error) or "The audit archive store is unavailable.",
        ) from error
    except AuditArchiveVerificationFailed as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=error.code,
            message=str(error) or "The audit archive failed integrity checks.",
        ) from error
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditArchiveDownloadResponse.model_validate(result, from_attributes=True)


@governance_router.get(
    "/legal-holds",
    response_model=list[AuditLegalHoldResponse],
    responses={401: {"model": ErrorResponse}},
)
async def list_legal_holds(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[AuditLegalHoldResponse]:
    _require_governance_owner(principal)
    holds = await _get_governance_service(request).list_legal_holds(
        tenant_id=UUID(principal.tenant_id)
    )
    return [AuditLegalHoldResponse.model_validate(hold, from_attributes=True) for hold in holds]


@governance_router.post(
    "/legal-holds",
    response_model=AuditLegalHoldResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_legal_hold(
    payload: AuditLegalHoldCreateRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditLegalHoldResponse:
    context = get_request_context()
    try:
        hold = await _get_governance_service(request).create_legal_hold(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            name=payload.name,
            reason=payload.reason,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            starts_at=payload.starts_at,
            expires_at=payload.expires_at,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditLegalHoldResponse.model_validate(hold, from_attributes=True)


@governance_router.delete(
    "/legal-holds/{hold_id}",
    response_model=AuditLegalHoldResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def release_legal_hold(
    hold_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> AuditLegalHoldResponse:
    context = get_request_context()
    try:
        hold = await _get_governance_service(request).release_legal_hold(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            hold_id=hold_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except AuditGovernanceError as error:
        raise _governance_api_error(error) from error
    return AuditLegalHoldResponse.model_validate(hold, from_attributes=True)


def _governance_api_error(error: AuditGovernanceError) -> ApiError:
    if isinstance(error, AuditGovernanceForbidden):
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=error.code,
            message="Only a tenant owner can manage audit governance.",
        )
    if isinstance(error, AuditGovernanceNotFound):
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=error.code,
            message="The audit governance resource was not found.",
        )
    if isinstance(error, AuditGovernanceInvalid):
        return ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=error.code,
            message=str(error) or "The audit governance request is invalid.",
        )
    if isinstance(error, AuditArchiveVerificationFailed):
        return ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=error.code,
            message=str(error) or "The audit archive failed integrity checks.",
        )
    return ApiError(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=error.code,
        message="The audit governance request could not be completed.",
    )


def _require_governance_owner(principal: PrincipalContext) -> None:
    if principal.role != MembershipRole.OWNER.value:
        raise _governance_api_error(AuditGovernanceForbidden())
