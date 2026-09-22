from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field, SecretStr
from starlette.responses import RedirectResponse

from enterprise_doc_api.browser_auth.github import GitHubEmailRequired, GitHubFailure
from enterprise_doc_api.browser_auth.http import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    browser_error,
    cookie_value,
    invalid_credentials,
    require_browser_credential,
    service_for,
    settings_for,
    verify_context,
    verify_site,
)
from enterprise_doc_api.browser_auth.oidc import OidcFailure
from enterprise_doc_api.browser_auth.provider import BrowserIdentityClient
from enterprise_doc_api.browser_auth.settings import GITHUB_AUTHORIZATION_ISSUER
from enterprise_doc_api.errors import ApiError
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.admission.errors import (
    AdmissionAccountConflict,
    AdmissionBusy,
    AdmissionError,
    AdmissionInvalid,
)
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.browser_sessions.contracts import (
    BrowserSessionSnapshot,
    BrowserTenantChoice,
    IssuedBrowserSession,
    csrf_token,
)
from enterprise_doc_core.browser_sessions.errors import BrowserSessionError, BrowserSessionInvalid

router = APIRouter(prefix="/auth", tags=["browser-auth"])
_LOGGER = logging.getLogger("enterprise_doc_api.browser_auth")


class BrowserAnonymousResponse(ApiModel):
    status: Literal["disabled", "anonymous"]


class BrowserGitHubAnonymousResponse(ApiModel):
    status: Literal["anonymous"] = "anonymous"
    login_provider: Literal["github"] = "github"


class BrowserTenantResponse(ApiModel):
    tenant_id: UUID
    name: str
    actor_id: UUID
    role: Literal["owner", "member"]


class BrowserAuthenticatedResponse(ApiModel):
    status: Literal["authenticated"] = "authenticated"
    email: str
    expires_at: datetime
    context_version: str
    csrf_token: str
    current_tenant: BrowserTenantResponse | None


class BrowserGitHubAuthenticatedResponse(BrowserAuthenticatedResponse):
    login_provider: Literal["github"] = "github"


BrowserVerifiedResponse = BrowserAuthenticatedResponse | BrowserGitHubAuthenticatedResponse
BrowserSessionResponse = (
    BrowserVerifiedResponse | BrowserAnonymousResponse | BrowserGitHubAnonymousResponse
)


class SelectTenantRequest(ApiModel):
    tenant_id: UUID


class AcceptAdmissionRequest(ApiModel):
    token: SecretStr = Field(min_length=48, max_length=48)
    tenant_name: str = Field(min_length=1, max_length=200)


class AcceptAdmissionResponse(ApiModel):
    tenant_id: UUID
    replayed: bool


def _tenant(choice: BrowserTenantChoice) -> BrowserTenantResponse:
    return BrowserTenantResponse(
        tenant_id=choice.tenant_id,
        name=choice.name,
        actor_id=choice.user_id,
        role=cast(Literal["owner", "member"], choice.role),
    )


def _snapshot(
    snapshot: BrowserSessionSnapshot, credential: SecretStr, *, github: bool = False
) -> BrowserVerifiedResponse:
    response_type = BrowserGitHubAuthenticatedResponse if github else BrowserAuthenticatedResponse
    return response_type(
        email=snapshot.identity.email,
        expires_at=snapshot.expires_at,
        context_version=snapshot.context_version,
        csrf_token=csrf_token(credential),
        current_tenant=_tenant(snapshot.selected) if snapshot.selected is not None else None,
    )


def _set_session(response: Response, issued: IssuedBrowserSession) -> None:
    remaining = max(0, int((issued.snapshot.expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        issued.credential.get_secret_value(),
        max_age=remaining,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


@router.get("/session", response_model=BrowserSessionResponse)
async def session_status(
    request: Request,
) -> BrowserSessionResponse:
    if request.headers.getlist("Authorization"):
        raise invalid_credentials()
    verify_site(request)
    if not settings_for(request).enabled:
        return BrowserAnonymousResponse(status="disabled")
    anonymous: BrowserSessionResponse = (
        BrowserGitHubAnonymousResponse()
        if settings_for(request).provider == "github"
        else BrowserAnonymousResponse(status="anonymous")
    )
    credential = cookie_value(request, SESSION_COOKIE)
    if credential is None:
        return anonymous
    try:
        snapshot = await service_for(request).get_session(credential=credential)
    except BrowserSessionInvalid:
        return anonymous
    except BrowserSessionError as error:
        raise browser_error(error) from None
    return _snapshot(snapshot, credential, github=settings_for(request).provider == "github")


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    if request.query_params or request.headers.getlist("Authorization"):
        raise invalid_credentials()
    settings = settings_for(request)
    service = service_for(request)
    # This entry is allowed for top-level navigation from a sign-in link.
    origins = request.headers.getlist("Origin")
    if origins and origins != [settings.web_origin]:
        raise invalid_credentials()
    peer = request.client.host if request.client is not None else "unknown"
    rate_key = hmac.new(
        request.app.state.auth_settings.signing_key.get_secret_value().encode(),
        peer.encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        start = await service.begin_login(
            rate_key=rate_key,
            previous_credential=cookie_value(request, SESSION_COOKIE),
            previous_login_verifier=cookie_value(request, LOGIN_COOKIE),
        )
    except BrowserSessionError as error:
        raise browser_error(error) from None
    identity_client = cast(BrowserIdentityClient, request.app.state.browser_identity_client)
    target = identity_client.authorization_url(
        state=start.state, nonce=start.nonce, verifier=start.verifier
    )
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        LOGIN_COOKIE,
        start.verifier.get_secret_value(),
        max_age=settings.login_ttl_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    settings = settings_for(request)
    service = service_for(request)
    failure = RedirectResponse(
        settings.web_origin + "/#/signin?error=sign_in_failed", status_code=303
    )

    def reject(reason: str) -> RedirectResponse:
        _LOGGER.warning(
            "browser_sign_in_failed",
            extra={"event_data": {"provider": settings.provider, "reason": reason}},
        )
        return failure

    # Never copy provider text, authorization codes, or state to a response body.
    try:
        if request.headers.getlist("Authorization"):
            return reject("unexpected_authorization_header")
        states, codes = request.query_params.getlist("state"), request.query_params.getlist("code")
        issuers = request.query_params.getlist("iss")
        # RFC 9207 identifies the OAuth authorization server. The persisted GitHub
        # identity namespace remains https://github.com for existing bindings.
        response_issuer = (
            GITHUB_AUTHORIZATION_ISSUER if settings.provider == "github" else settings.issuer
        )
        if len(states) != 1:
            return reject("invalid_state_count")
        if issuers and issuers != [response_issuer]:
            return reject("issuer_mismatch")
        verifier = cookie_value(request, LOGIN_COOKIE)
        if verifier is None:
            return reject("missing_login_cookie")
        previous = cookie_value(request, SESSION_COOKIE)
        claim = await service.claim_login(
            state=SecretStr(states[0]), verifier=verifier, previous_credential=previous
        )
        if request.query_params.getlist("error"):
            return reject("provider_error")
        if len(codes) != 1:
            return reject("invalid_code_count")
        identity_client = cast(BrowserIdentityClient, request.app.state.browser_identity_client)
        identity = await identity_client.exchange(
            code=SecretStr(codes[0]),
            verifier=verifier,
            nonce_digest=claim.nonce_digest,
            started_at=claim.started_at,
        )
        issued = await service.complete_login(
            attempt_id=claim.attempt_id, identity=identity, previous_credential=previous
        )
    except GitHubEmailRequired:
        return RedirectResponse(
            settings.web_origin + "/#/signin?error=github_email_required", status_code=303
        )
    except (ApiError, BrowserSessionError, OidcFailure, GitHubFailure) as error:
        return reject(type(error).__name__)
    response = RedirectResponse(settings.web_origin + "/", status_code=303)
    _set_session(response, issued)
    # A stale callback must not delete a newer login attempt's cookie. It expires naturally.
    return response


@router.get("/tenants", response_model=list[BrowserTenantResponse])
async def tenants(request: Request) -> list[BrowserTenantResponse]:
    credential = require_browser_credential(request)
    version = verify_context(request, credential)
    try:
        choices = await service_for(request).list_tenants(
            credential=credential, context_version=version
        )
    except BrowserSessionError as error:
        raise browser_error(error) from None
    return [_tenant(choice) for choice in choices]


@router.post("/tenant", response_model=BrowserVerifiedResponse)
async def select_tenant(
    payload: SelectTenantRequest, request: Request, response: Response
) -> BrowserVerifiedResponse:
    credential = require_browser_credential(request)
    try:
        version = verify_context(request, credential, mutation=True)
        issued = await service_for(request).select_tenant(
            credential=credential, context_version=version, tenant_id=payload.tenant_id
        )
    except BrowserSessionError as error:
        raise browser_error(error) from None
    _set_session(response, issued)
    return _snapshot(
        issued.snapshot, issued.credential, github=settings_for(request).provider == "github"
    )


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    credential = require_browser_credential(request)
    try:
        version = verify_context(request, credential, mutation=True)
        await service_for(request).logout(credential=credential, context_version=version)
    except BrowserSessionError as error:
        raise browser_error(error) from None
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"revoked": True}


@router.post("/admission/accept", response_model=AcceptAdmissionResponse)
async def accept_admission(
    payload: AcceptAdmissionRequest, request: Request
) -> AcceptAdmissionResponse:
    credential = require_browser_credential(request)
    try:
        version = verify_context(request, credential, mutation=True)
        snapshot = await service_for(request).get_session(
            credential=credential, context_version=version
        )
    except BrowserSessionError as error:
        raise browser_error(error) from None
    admission = cast(TenantAdmissionService, request.app.state.browser_admission_service)
    try:
        result = await admission.accept(
            token=payload.token, identity=snapshot.identity, tenant_name=payload.tenant_name
        )
    except AdmissionError as error:
        status = (
            409
            if isinstance(error, (AdmissionAccountConflict, AdmissionBusy))
            else 422
            if isinstance(error, AdmissionInvalid)
            else 403
        )
        raise ApiError(
            status_code=status,
            code=error.code,
            message="The enterprise admission could not be accepted.",
        ) from None
    return AcceptAdmissionResponse(tenant_id=result.tenant_id, replayed=result.replayed)
