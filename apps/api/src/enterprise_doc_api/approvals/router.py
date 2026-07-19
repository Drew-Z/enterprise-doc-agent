from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import Field

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.agents import (
    ApprovalAlreadyDecided,
    ApprovalDecisionResult,
    ApprovalError,
    ApprovalInputInvalid,
    ApprovalIntegrityError,
    ApprovalNotFound,
    ApprovalPrincipalForbidden,
    ApprovalRequestResult,
    ApprovalRunNotWaiting,
    ApprovalTargetChanged,
    ApprovalTargetMismatch,
    DecideApprovalInput,
)
from enterprise_doc_core.context import PrincipalContext, get_request_context


class ApprovalServiceProtocol(Protocol):
    async def get(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequestResult: ...

    async def decide(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None,
        approval_id: UUID,
        idempotency_key: str,
        request: DecideApprovalInput,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ApprovalDecisionResult: ...


class ApprovalDecisionRequest(ApiModel):
    decision: Literal["approved", "rejected"]
    operation: Literal["publish_artifact"]
    target_resource_type: Literal["agent_artifact"]
    target_resource_id: UUID
    target_document_version_id: UUID
    target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=1000)


class ApprovalDecisionResponse(ApiModel):
    approval_id: UUID
    run_id: UUID
    status: str
    decision: str
    resume_job_id: UUID
    resume_execution_id: UUID
    decision_fingerprint: str
    replayed: bool
    decided_at: datetime


class ApprovalRequestResponse(ApiModel):
    approval_id: UUID
    run_id: UUID
    status: str
    operation: str
    target_resource_type: str
    target_resource_id: UUID
    target_document_version_id: UUID
    target_fingerprint: str
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    can_decide: bool


router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get(
    "/{approval_id}",
    response_model=ApprovalRequestResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_approval(
    approval_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ApprovalRequestResponse:
    service = cast(ApprovalServiceProtocol, request.app.state.approval_service)
    try:
        result = await service.get(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            approval_id=approval_id,
        )
    except ApprovalError as error:
        raise _approval_api_error(error) from error
    return _approval_request_response(result)


@router.post(
    "/{approval_id}/decisions",
    response_model=ApprovalDecisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": ApprovalDecisionResponse, "description": "Idempotent replay."},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    response: Response,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApprovalDecisionResponse:
    if idempotency_key is None:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="idempotency_key_required",
            message="An Idempotency-Key header is required.",
        )
    try:
        tenant_id = UUID(principal.tenant_id)
        actor_id = UUID(principal.actor_id)
    except ValueError as error:
        raise _approval_api_error(ApprovalPrincipalForbidden()) from error
    service = cast(ApprovalServiceProtocol, request.app.state.approval_service)
    context = get_request_context()
    try:
        result = await service.decide(
            tenant_id=tenant_id,
            actor_id=actor_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            request=DecideApprovalInput(
                decision=payload.decision,
                operation=payload.operation,
                target_resource_type=payload.target_resource_type,
                target_resource_id=payload.target_resource_id,
                target_document_version_id=payload.target_document_version_id,
                target_fingerprint=payload.target_fingerprint,
                comment=payload.comment,
            ),
            request_id=context.request_id if context is not None else None,
            correlation_id=context.correlation_id if context is not None else None,
        )
    except ApprovalError as error:
        raise _approval_api_error(error) from error
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return ApprovalDecisionResponse(
        approval_id=result.approval_id,
        run_id=result.run_id,
        status=result.status,
        decision=result.decision,
        resume_job_id=result.resume_job_id,
        resume_execution_id=result.resume_execution_id,
        decision_fingerprint=result.decision_fingerprint,
        replayed=result.replayed,
        decided_at=result.decided_at,
    )


def _approval_api_error(error: ApprovalError) -> ApiError:
    if isinstance(error, ApprovalNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ApprovalPrincipalForbidden):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, ApprovalInputInvalid):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(
        error,
        (
            ApprovalAlreadyDecided,
            ApprovalRunNotWaiting,
            ApprovalTargetChanged,
            ApprovalTargetMismatch,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, ApprovalIntegrityError):
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return ApiError(status_code=status_code, code=error.code, message=error.message)


def _approval_request_response(result: ApprovalRequestResult) -> ApprovalRequestResponse:
    return ApprovalRequestResponse(
        approval_id=result.approval_id,
        run_id=result.run_id,
        status=result.status,
        operation=result.operation,
        target_resource_type=result.target_resource_type,
        target_resource_id=result.target_resource_id,
        target_document_version_id=result.target_document_version_id,
        target_fingerprint=result.target_fingerprint,
        requested_at=result.requested_at,
        expires_at=result.expires_at,
        decided_at=result.decided_at,
        can_decide=result.can_decide,
    )


__all__ = [
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalRequestResponse",
    "ApprovalServiceProtocol",
    "router",
]
