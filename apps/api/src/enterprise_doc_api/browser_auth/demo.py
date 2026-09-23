import re
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import SecretStr

from enterprise_doc_api.browser_auth.http import (
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
from enterprise_doc_api.errors import ApiError
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.browser_sessions.contracts import csrf_token
from enterprise_doc_core.browser_sessions.errors import BrowserSessionError, BrowserSessionInvalid
from enterprise_doc_core.demo.service import DemoService, DemoSnapshot
from enterprise_doc_core.demo.settings import (
    ATTEMPT_LIMIT,
    FILE_SIZE_LIMIT,
    STORAGE_LIMIT,
    UPLOAD_LIMIT,
)

router = APIRouter(tags=["public-demo"])


class DemoAnonymousResponse(ApiModel):
    status: Literal["anonymous"] = "anonymous"
    login_provider: Literal["github", "oidc"]
    demo_available: Literal[True] = True


class DemoTenantResponse(ApiModel):
    tenant_id: UUID
    actor_id: UUID
    name: str = "演示企业"
    role: Literal["owner"] = "owner"


class DemoAuthenticatedResponse(ApiModel):
    status: Literal["authenticated"] = "authenticated"
    email: None = None
    demo: Literal[True] = True
    login_provider: Literal["github", "oidc"]
    expires_at: datetime
    context_version: str
    csrf_token: str
    current_tenant: DemoTenantResponse


class DemoStartRequest(ApiModel):
    pass


def demo_service(request: Request) -> DemoService | None:
    return cast(DemoService | None, getattr(request.app.state, "demo_service", None))


def demo_response(
    snapshot: DemoSnapshot, credential: SecretStr, provider: Literal["github", "oidc"]
) -> DemoAuthenticatedResponse:
    return DemoAuthenticatedResponse(
        expires_at=snapshot.expires_at,
        context_version=snapshot.context_version,
        login_provider=provider,
        csrf_token=csrf_token(credential),
        current_tenant=DemoTenantResponse(tenant_id=snapshot.tenant_id, actor_id=snapshot.actor_id),
    )


def allow_demo_operation(method: str, path: str) -> None:
    # Explicit capability boundary. New administrative routes are not implicitly public.
    identifier = r"[0-9a-fA-F-]{36}"
    routes = {
        "GET": [
            r"/api/session",
            r"/api/demo",
            r"/api/documents",
            r"/api/tenant-usage",
            rf"/api/upload-sessions/{identifier}",
            rf"/api/jobs/{identifier}",
            r"/api/presales",
            rf"/api/presales/{identifier}",
            rf"/api/presales/{identifier}/export",
        ],
        "POST": [
            r"/api/upload-sessions",
            rf"/api/upload-sessions/{identifier}/parts/[1-9][0-9]*/presign",
            rf"/api/upload-sessions/{identifier}/complete",
            r"/api/presales",
            rf"/api/presales/{identifier}/rows/{identifier}/generate",
            rf"/api/jobs/{identifier}/cancel",
        ],
        "PUT": [rf"/api/presales/{identifier}/rows/{identifier}/review"],
        "DELETE": [rf"/api/upload-sessions/{identifier}"],
    }
    if not any(re.fullmatch(pattern, path) for pattern in routes.get(method, [])):
        raise ApiError(
            status_code=403,
            code="demo_operation_forbidden",
            message="This operation requires a regular workspace.",
        )


@router.post("/auth/demo", response_model=DemoAuthenticatedResponse)
async def start_demo(
    _: DemoStartRequest, request: Request, response: Response
) -> DemoAuthenticatedResponse:
    verify_site(request, mutation=True)
    if request.headers.getlist("Authorization"):
        raise invalid_credentials()
    service = demo_service(request)
    if service is None or not service.settings.enabled:
        raise ApiError(
            status_code=503, code="demo_unavailable", message="The public demo is unavailable."
        )
    credential = cookie_value(request, SESSION_COOKIE)
    if credential is not None:
        try:
            existing = await service.get(credential)
            if existing is not None:
                return demo_response(existing, credential, settings_for(request).provider)
            await service_for(request).get_session(credential=credential)
        except BrowserSessionInvalid:
            pass
        except BrowserSessionError as error:
            raise browser_error(error) from None
        else:
            raise ApiError(
                status_code=409,
                code="demo_signout_required",
                message="Sign out before starting a demo.",
            )
    issued = await service.start()
    response.set_cookie(
        SESSION_COOKIE,
        issued.credential.get_secret_value(),
        max_age=max(0, int((issued.snapshot.expires_at - datetime.now(UTC)).total_seconds())),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return demo_response(issued.snapshot, issued.credential, settings_for(request).provider)


@router.get("/api/demo")
async def demo_usage(request: Request) -> dict[str, int]:
    credential = require_browser_credential(request)
    version = verify_context(request, credential)
    service = demo_service(request)
    if service is None:
        raise browser_error(BrowserSessionInvalid())
    try:
        snapshot = await service.get(credential, version)
        if snapshot is None:
            raise BrowserSessionInvalid()
    except BrowserSessionError as error:
        raise browser_error(error) from None
    return {
        "attemptsUsed": snapshot.attempts_used,
        "attemptLimit": ATTEMPT_LIMIT,
        "uploadLimit": UPLOAD_LIMIT,
        "maxFileBytes": FILE_SIZE_LIMIT,
        "storageBytes": STORAGE_LIMIT,
    }
