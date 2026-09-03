from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import StringConstraints

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.identity.service import (
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingError,
    ExternalIdentityBindingForbidden,
    ExternalIdentityBindingNotFound,
    ExternalIdentityBindingResult,
    ExternalIdentityBindingTargetNotFound,
    ExternalIdentityMemberResult,
)


class ExternalIdentityBindingServiceProtocol(Protocol):
    async def list_bindings(
        self, **kwargs: object
    ) -> tuple[ExternalIdentityBindingResult, ...]: ...

    async def list_active_members(
        self, **kwargs: object
    ) -> tuple[ExternalIdentityMemberResult, ...]: ...

    async def create_binding(self, **kwargs: object) -> ExternalIdentityBindingResult: ...

    async def activate_binding(self, **kwargs: object) -> ExternalIdentityBindingResult: ...

    async def deactivate_binding(self, **kwargs: object) -> ExternalIdentityBindingResult: ...


BoundIdentityClaim = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class ExternalIdentityBindingCreateRequest(ApiModel):
    issuer: BoundIdentityClaim
    subject: BoundIdentityClaim
    user_id: UUID


class ExternalIdentityBindingResponse(ApiModel):
    binding_id: UUID
    tenant_id: UUID
    issuer: str
    subject: str
    user_id: UUID
    user_email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ExternalIdentityMemberResponse(ApiModel):
    user_id: UUID
    email: str
    role: str


router = APIRouter(prefix="/api/identity-bindings", tags=["identity"])


@router.get(
    "/members",
    response_model=list[ExternalIdentityMemberResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_external_identity_members(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    q: Annotated[str | None, Query(max_length=320)] = None,
) -> list[ExternalIdentityMemberResponse]:
    _require_owner(principal)
    service = _get_service(request)
    try:
        members = await service.list_active_members(
            tenant_id=UUID(principal.tenant_id),
            role=principal.role,
            query=q,
            limit=50,
        )
    except ExternalIdentityBindingError as error:
        raise _api_error(error) from error
    return [
        ExternalIdentityMemberResponse.model_validate(member, from_attributes=True)
        for member in members
    ]


@router.get(
    "",
    response_model=list[ExternalIdentityBindingResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_external_identity_bindings(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> list[ExternalIdentityBindingResponse]:
    _require_owner(principal)
    service = _get_service(request)
    try:
        bindings = await service.list_bindings(
            tenant_id=UUID(principal.tenant_id),
            role=principal.role,
        )
    except ExternalIdentityBindingError as error:
        raise _api_error(error) from error
    return [_response(binding) for binding in bindings]


@router.post(
    "",
    response_model=ExternalIdentityBindingResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_external_identity_binding(
    payload: ExternalIdentityBindingCreateRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ExternalIdentityBindingResponse:
    _require_owner(principal)
    service = _get_service(request)
    context = get_request_context()
    try:
        binding = await service.create_binding(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            issuer=payload.issuer,
            subject=payload.subject,
            user_id=payload.user_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ExternalIdentityBindingError as error:
        raise _api_error(error) from error
    return _response(binding)


@router.delete(
    "/{binding_id}",
    response_model=ExternalIdentityBindingResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def deactivate_external_identity_binding(
    binding_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ExternalIdentityBindingResponse:
    _require_owner(principal)
    service = _get_service(request)
    context = get_request_context()
    try:
        binding = await service.deactivate_binding(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            binding_id=binding_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ExternalIdentityBindingError as error:
        raise _api_error(error) from error
    return _response(binding)


@router.post(
    "/{binding_id}/activate",
    response_model=ExternalIdentityBindingResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def activate_external_identity_binding(
    binding_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> ExternalIdentityBindingResponse:
    _require_owner(principal)
    service = _get_service(request)
    context = get_request_context()
    try:
        binding = await service.activate_binding(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            binding_id=binding_id,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ExternalIdentityBindingError as error:
        raise _api_error(error) from error
    return _response(binding)


def _get_service(request: Request) -> ExternalIdentityBindingServiceProtocol:
    service = cast(
        ExternalIdentityBindingServiceProtocol | None,
        getattr(request.app.state, "external_identity_binding_service", None),
    )
    if service is None:
        raise ApiError(
            status_code=503,
            code="external_identity_binding_unavailable",
            message="External identity binding management is unavailable.",
        )
    return service


def _require_owner(principal: PrincipalContext) -> None:
    if principal.role != "owner":
        error = ExternalIdentityBindingForbidden()
        raise _api_error(error) from error


def _response(binding: ExternalIdentityBindingResult) -> ExternalIdentityBindingResponse:
    return ExternalIdentityBindingResponse.model_validate(binding, from_attributes=True)


def _api_error(error: ExternalIdentityBindingError) -> ApiError:
    if isinstance(error, ExternalIdentityBindingForbidden):
        return ApiError(
            status_code=403,
            code=error.code,
            message="Only a tenant owner can manage external identity bindings.",
        )
    if isinstance(error, ExternalIdentityBindingTargetNotFound):
        return ApiError(
            status_code=404,
            code=error.code,
            message="The target user is not an active member of this tenant.",
        )
    if isinstance(error, ExternalIdentityBindingNotFound):
        return ApiError(
            status_code=404,
            code=error.code,
            message="The external identity binding was not found.",
        )
    if isinstance(error, ExternalIdentityBindingConflict):
        return ApiError(
            status_code=409,
            code=error.code,
            message="This external identity is already bound in the tenant.",
        )
    return ApiError(
        status_code=422,
        code=error.code,
        message="The external identity binding request is invalid.",
    )
