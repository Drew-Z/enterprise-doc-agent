from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, Request, status

from enterprise_doc_api.auth.dependencies import get_current_principal
from enterprise_doc_api.auth.jwt import InvalidBearerToken, JwtTokenCodec
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.auth import LocalTokenRevocationResult
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.identity import MembershipRole


class SessionCapabilitiesResponse(ApiModel):
    document_read: bool
    document_write: bool
    agent_run_create: bool
    audit_read: bool
    audit_export: bool
    approval_decide: bool


class SessionResponse(ApiModel):
    tenant_id: str
    actor_id: str
    role: str
    capabilities: SessionCapabilitiesResponse


class SessionLogoutResponse(ApiModel):
    revoked: bool
    already_revoked: bool
    revoked_at: datetime


class LocalTokenRevocationServiceProtocol(Protocol):
    async def revoke(self, **kwargs: object) -> LocalTokenRevocationResult: ...


router = APIRouter(prefix="/api/session", tags=["session"])


def _capabilities_for(principal: PrincipalContext) -> SessionCapabilitiesResponse:
    """Expose UI hints derived from the authenticated server-side principal.

    This response deliberately mirrors, but never replaces, authorization in
    the individual mutation routes. The browser must not become the authority
    for a tenant role or for a privileged operation.
    """

    is_owner = principal.role == MembershipRole.OWNER.value
    return SessionCapabilitiesResponse(
        document_read=True,
        document_write=True,
        agent_run_create=True,
        audit_read=True,
        audit_export=is_owner,
        approval_decide=is_owner,
    )


@router.get("", response_model=SessionResponse, responses={401: {"model": ErrorResponse}})
async def get_session(
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> SessionResponse:
    return SessionResponse(
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        role=principal.role,
        capabilities=_capabilities_for(principal),
    )


@router.post(
    "/logout",
    response_model=SessionLogoutResponse,
    responses={
        401: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def logout_session(
    request: Request,
    principal: Annotated[PrincipalContext, Depends(get_current_principal)],
) -> SessionLogoutResponse:
    raw_token = getattr(request.state, "auth_token", None)
    if not isinstance(raw_token, str):
        raise InvalidBearerToken()
    settings = request.app.state.auth_settings
    try:
        claims = JwtTokenCodec(settings).decode(raw_token)
    except InvalidBearerToken as error:
        if settings.external_auth_enabled:
            raise _external_logout_unsupported() from error
        raise
    if str(claims.tenant_id) != principal.tenant_id or str(claims.actor_id) != principal.actor_id:
        raise InvalidBearerToken()

    service = cast(
        LocalTokenRevocationServiceProtocol | None,
        getattr(request.app.state, "token_revocation_service", None),
    )
    if service is None:
        raise _service_unavailable()
    context = get_request_context()
    result = await service.revoke(
        tenant_id=claims.tenant_id,
        actor_id=claims.actor_id,
        token_id=claims.token_id,
        issued_at=claims.issued_at,
        expires_at=claims.expires_at,
        reason="logout",
        request_id=getattr(context, "request_id", None),
        correlation_id=getattr(context, "correlation_id", None),
    )
    return SessionLogoutResponse(
        revoked=not result.already_revoked,
        already_revoked=result.already_revoked,
        revoked_at=result.revoked_at,
    )


def _external_logout_unsupported() -> ApiError:
    return ApiError(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="session_logout_external_unsupported",
        message="Server-side logout is only available for local JWT sessions.",
    )


def _service_unavailable() -> ApiError:
    return ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="session_logout_unavailable",
        message="Server-side logout is temporarily unavailable.",
    )
