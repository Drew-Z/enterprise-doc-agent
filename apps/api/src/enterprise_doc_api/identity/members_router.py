from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import StringConstraints

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.identity.membership_service import (
    MembershipAdministrationConflict,
    MembershipAdministrationError,
    MembershipAdministrationForbidden,
    MembershipAdministrationInvalid,
    MembershipAdministrationNotFound,
    MembershipAdministrationResult,
    MembershipLastOwnerRequired,
    MembershipSelfMutationForbidden,
)


class MembershipAdministrationServiceProtocol(Protocol):
    async def list_members(
        self, **kwargs: object
    ) -> tuple[MembershipAdministrationResult, ...]: ...

    async def provision_member(self, **kwargs: object) -> MembershipAdministrationResult: ...

    async def change_role(self, **kwargs: object) -> MembershipAdministrationResult: ...

    async def deactivate_member(self, **kwargs: object) -> MembershipAdministrationResult: ...

    async def activate_member(self, **kwargs: object) -> MembershipAdministrationResult: ...


MemberEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=320),
]
MemberRole = Literal["owner", "member"]


class MemberProvisionRequest(ApiModel):
    email: MemberEmail
    role: MemberRole


class MemberRoleRequest(ApiModel):
    role: MemberRole


class MemberResponse(ApiModel):
    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    email: str
    role: MemberRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


router = APIRouter(prefix="/api/members", tags=["identity"])


@router.get(
    "",
    response_model=list[MemberResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_members(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    q: Annotated[str | None, Query(max_length=320)] = None,
) -> list[MemberResponse]:
    _require_owner(principal)
    service = _get_service(request)
    try:
        members = await service.list_members(
            tenant_id=UUID(principal.tenant_id),
            role=principal.role,
            query=q,
            limit=100,
        )
    except MembershipAdministrationError as error:
        raise _api_error(error) from error
    return [MemberResponse.model_validate(member, from_attributes=True) for member in members]


@router.post(
    "",
    response_model=MemberResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def provision_member(
    payload: MemberProvisionRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> MemberResponse:
    _require_owner(principal)
    service = _get_service(request)
    context = get_request_context()
    try:
        member = await service.provision_member(
            tenant_id=UUID(principal.tenant_id),
            actor_id=UUID(principal.actor_id),
            role=principal.role,
            email=payload.email,
            member_role=payload.role,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except MembershipAdministrationError as error:
        raise _api_error(error) from error
    return MemberResponse.model_validate(member, from_attributes=True)


@router.put(
    "/{membership_id}/role",
    response_model=MemberResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def change_member_role(
    membership_id: UUID,
    payload: MemberRoleRequest,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> MemberResponse:
    return await _mutate_member(
        request=request,
        principal=principal,
        method="change_role",
        membership_id=membership_id,
        member_role=payload.role,
    )


@router.delete(
    "/{membership_id}",
    response_model=MemberResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def deactivate_member(
    membership_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> MemberResponse:
    return await _mutate_member(
        request=request,
        principal=principal,
        method="deactivate_member",
        membership_id=membership_id,
    )


@router.post(
    "/{membership_id}/activate",
    response_model=MemberResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def activate_member(
    membership_id: UUID,
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> MemberResponse:
    return await _mutate_member(
        request=request,
        principal=principal,
        method="activate_member",
        membership_id=membership_id,
    )


async def _mutate_member(
    *,
    request: Request,
    principal: PrincipalContext,
    method: Literal["change_role", "deactivate_member", "activate_member"],
    membership_id: UUID,
    member_role: MemberRole | None = None,
) -> MemberResponse:
    _require_owner(principal)
    service = _get_service(request)
    context = get_request_context()
    kwargs: dict[str, object] = {
        "tenant_id": UUID(principal.tenant_id),
        "actor_id": UUID(principal.actor_id),
        "role": principal.role,
        "membership_id": membership_id,
        "request_id": context.request_id if context else None,
        "correlation_id": context.correlation_id if context else None,
    }
    if member_role is not None:
        kwargs["member_role"] = member_role
    try:
        member = await getattr(service, method)(**kwargs)
    except MembershipAdministrationError as error:
        raise _api_error(error) from error
    return MemberResponse.model_validate(member, from_attributes=True)


def _get_service(request: Request) -> MembershipAdministrationServiceProtocol:
    service = cast(
        MembershipAdministrationServiceProtocol | None,
        getattr(request.app.state, "membership_administration_service", None),
    )
    if service is None:
        raise ApiError(
            status_code=503,
            code="membership_administration_unavailable",
            message="Tenant membership administration is unavailable.",
        )
    return service


def _require_owner(principal: PrincipalContext) -> None:
    if principal.role != "owner":
        error = MembershipAdministrationForbidden()
        raise _api_error(error) from error


def _api_error(error: MembershipAdministrationError) -> ApiError:
    if isinstance(error, MembershipAdministrationForbidden):
        return ApiError(
            status_code=403,
            code=error.code,
            message="Only a tenant owner can manage memberships.",
        )
    if isinstance(error, MembershipAdministrationNotFound):
        return ApiError(
            status_code=404,
            code=error.code,
            message="The tenant membership was not found.",
        )
    if isinstance(error, MembershipLastOwnerRequired):
        return ApiError(
            status_code=409,
            code=error.code,
            message="At least one active tenant owner must remain.",
        )
    if isinstance(error, MembershipSelfMutationForbidden):
        return ApiError(
            status_code=409,
            code=error.code,
            message="Use another owner to change or deactivate your own membership.",
        )
    if isinstance(error, MembershipAdministrationConflict):
        return ApiError(
            status_code=409,
            code=error.code,
            message="The membership conflicts with the current user state.",
        )
    if isinstance(error, MembershipAdministrationInvalid):
        return ApiError(
            status_code=422,
            code=error.code,
            message="The membership administration request is invalid.",
        )
    return ApiError(
        status_code=422,
        code=error.code,
        message="The membership administration request could not be completed.",
    )
