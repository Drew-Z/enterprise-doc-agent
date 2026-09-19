from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field, SecretStr, field_validator

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.browser_auth.http import (
    browser_error,
    require_browser_credential,
    service_for,
    verify_context,
)
from enterprise_doc_api.errors import ApiError
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity, normalized_email
from enterprise_doc_core.browser_sessions.errors import BrowserSessionError
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.identity.seats import MembershipSeatLimitReached
from enterprise_doc_core.invitations.contracts import (
    CreateInvitation,
    InvitationMutation,
    InvitationOperation,
    InvitationState,
)
from enterprise_doc_core.invitations.errors import (
    InvitationAccountConflict,
    InvitationBusy,
    InvitationConflict,
    InvitationDenied,
    InvitationError,
    InvitationForbidden,
    InvitationIneligible,
    InvitationLimitReached,
    InvitationNotFound,
)
from enterprise_doc_core.invitations.service import MembershipInvitationService

router = APIRouter(prefix="/api/invitations", tags=["membership-invitations"])
auth_router = APIRouter(prefix="/auth/invitations", tags=["membership-invitations"])
Principal = Annotated[PrincipalContext, Depends(get_current_principal)]


class CreateInvitationRequest(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    operation_id: UUID
    _email = field_validator("email")(normalized_email)


class InvitationOperationRequest(ApiModel):
    expected_generation: int = Field(strict=True, ge=1, le=2**31 - 1)
    operation_id: UUID


class InvitationTokenRequest(ApiModel):
    token: SecretStr = Field(min_length=48, max_length=48, repr=False)


class InvitationResponse(ApiModel):
    invitation_id: UUID
    email: str
    state: InvitationState
    generation: int
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class InvitationMutationResponse(ApiModel):
    invitation: InvitationResponse
    replayed: bool
    token: str | None = Field(default=None, repr=False)


class MembershipSeatsResponse(ApiModel):
    active: int
    limit: int | None
    remaining: int | None


class InvitationListResponse(ApiModel):
    items: list[InvitationResponse]
    has_more: bool
    seats: MembershipSeatsResponse
    eligible: bool


class InvitationPreviewResponse(ApiModel):
    tenant_name: str
    expires_at: datetime
    state: InvitationState


class InvitationReceiptResponse(ApiModel):
    tenant_id: UUID
    tenant_name: str
    membership_id: UUID
    replayed: bool


def _error(error: InvitationError | MembershipSeatLimitReached) -> ApiError:
    status = (
        403
        if isinstance(error, (InvitationForbidden, InvitationDenied))
        else 404
        if isinstance(error, InvitationNotFound)
        else 409
        if isinstance(
            error,
            (
                InvitationConflict,
                InvitationAccountConflict,
                InvitationBusy,
                InvitationIneligible,
                MembershipSeatLimitReached,
            ),
        )
        else 429
        if isinstance(error, InvitationLimitReached)
        else 503
    )
    return ApiError(
        status_code=status,
        code=error.code,
        message="The membership invitation operation could not be completed.",
        headers={"Retry-After": "3600"} if status == 429 else None,
    )


def _service(request: Request) -> MembershipInvitationService:
    service = cast(
        MembershipInvitationService | None,
        getattr(request.app.state, "membership_invitation_service", None),
    )
    if service is None:
        raise ApiError(
            status_code=503,
            code="invitations_unavailable",
            message="Membership invitations are not enabled.",
        )
    return service


def _owner(principal: PrincipalContext) -> tuple[UUID, UUID]:
    if principal.role != "owner":
        raise _error(InvitationForbidden())
    return UUID(principal.tenant_id), UUID(principal.actor_id)


def _mutation(result: InvitationMutation) -> InvitationMutationResponse:
    return InvitationMutationResponse(
        invitation=InvitationResponse.model_validate(result.invitation, from_attributes=True),
        replayed=result.replayed,
        token=result.token.get_secret_value() if result.token is not None else None,
    )


async def _identity(request: Request) -> VerifiedAdmissionIdentity:
    credential = require_browser_credential(request)
    try:
        version = verify_context(request, credential, mutation=True)
        snapshot = await service_for(request).get_session(
            credential=credential,
            context_version=version,
        )
    except BrowserSessionError as error:
        raise browser_error(error) from None
    return snapshot.identity


@router.get("", response_model=InvitationListResponse)
async def list_invitations(request: Request, principal: Principal) -> InvitationListResponse:
    tenant_id, actor_id = _owner(principal)
    try:
        result = await _service(request).list_invitations(tenant_id=tenant_id, actor_id=actor_id)
    except InvitationError as error:
        raise _error(error) from None
    return InvitationListResponse(
        items=[
            InvitationResponse.model_validate(item, from_attributes=True) for item in result.items
        ],
        has_more=result.has_more,
        eligible=result.eligible,
        seats=MembershipSeatsResponse(
            active=result.seats.active,
            limit=result.seats.limit,
            remaining=result.seats.remaining,
        ),
    )


@router.post("", response_model=InvitationMutationResponse)
async def create_invitation(
    payload: CreateInvitationRequest,
    request: Request,
    principal: Principal,
) -> InvitationMutationResponse:
    tenant_id, actor_id = _owner(principal)
    context = get_request_context()
    try:
        result = await _service(request).create(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request=CreateInvitation(email=payload.email, operation_id=payload.operation_id),
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except InvitationError as error:
        raise _error(error) from None
    return _mutation(result)


@router.post("/{invitation_id}/regenerate", response_model=InvitationMutationResponse)
async def regenerate_invitation(
    invitation_id: UUID,
    payload: InvitationOperationRequest,
    request: Request,
    principal: Principal,
) -> InvitationMutationResponse:
    tenant_id, actor_id = _owner(principal)
    context = get_request_context()
    try:
        result = await _service(request).regenerate(
            tenant_id=tenant_id,
            actor_id=actor_id,
            invitation_id=invitation_id,
            request=InvitationOperation(
                operation_id=payload.operation_id,
                expected_generation=payload.expected_generation,
            ),
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except InvitationError as error:
        raise _error(error) from None
    return _mutation(result)


@router.post("/{invitation_id}/revoke", response_model=InvitationMutationResponse)
async def revoke_invitation(
    invitation_id: UUID,
    payload: InvitationOperationRequest,
    request: Request,
    principal: Principal,
) -> InvitationMutationResponse:
    tenant_id, actor_id = _owner(principal)
    context = get_request_context()
    try:
        result = await _service(request).revoke(
            tenant_id=tenant_id,
            actor_id=actor_id,
            invitation_id=invitation_id,
            request=InvitationOperation(
                operation_id=payload.operation_id,
                expected_generation=payload.expected_generation,
            ),
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except InvitationError as error:
        raise _error(error) from None
    return _mutation(result)


@auth_router.post("/inspect", response_model=InvitationPreviewResponse)
async def inspect_invitation(
    payload: InvitationTokenRequest,
    request: Request,
) -> InvitationPreviewResponse:
    identity = await _identity(request)
    try:
        result = await _service(request).inspect(token=payload.token, identity=identity)
    except InvitationError as error:
        raise _error(error) from None
    return InvitationPreviewResponse.model_validate(result, from_attributes=True)


@auth_router.post("/accept", response_model=InvitationReceiptResponse)
async def accept_invitation(
    payload: InvitationTokenRequest,
    request: Request,
) -> InvitationReceiptResponse:
    identity = await _identity(request)
    context = get_request_context()
    try:
        result = await _service(request).accept(
            token=payload.token,
            identity=identity,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except (InvitationError, MembershipSeatLimitReached) as error:
        raise _error(error) from None
    return InvitationReceiptResponse.model_validate(result, from_attributes=True)
