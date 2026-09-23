from __future__ import annotations

import hmac
import re
from typing import cast

from fastapi import Request
from pydantic import SecretStr
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.browser_sessions.contracts import csrf_token
from enterprise_doc_core.browser_sessions.errors import (
    BrowserContextStale,
    BrowserIdentityConflict,
    BrowserLoginInvalid,
    BrowserLoginRateLimited,
    BrowserPrincipalForbidden,
    BrowserSessionBusy,
    BrowserSessionError,
    BrowserSessionInvalid,
)
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.context import PrincipalContext, enrich_request_principal

SESSION_COOKIE = "__Host-docagent-session"
LOGIN_COOKIE = "__Host-docagent-login"
CONTEXT_HEADER = "X-Session-Context"
CSRF_HEADER = "X-CSRF-Token"


def settings_for(request: Request) -> BrowserAuthSettings:
    return cast(BrowserAuthSettings, request.app.state.browser_auth_settings)


def service_for(request: Request) -> BrowserSessionService:
    service = getattr(request.app.state, "browser_session_service", None)
    if service is None:
        raise ApiError(
            status_code=503,
            code="browser_auth_disabled",
            message="Browser sign-in is not available.",
        )
    return cast(BrowserSessionService, service)


def cookie_value(request: Request, name: str) -> SecretStr | None:
    values: list[str] = []
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            key, separator, value = part.strip().partition("=")
            if key == name:
                if separator != "=" or not value or len(value) > 256:
                    raise invalid_credentials()
                values.append(value)
    if len(values) > 1:
        raise invalid_credentials()
    return SecretStr(values[0]) if values else None


def invalid_credentials() -> ApiError:
    return ApiError(
        status_code=400,
        code="browser_credentials_invalid",
        message="Use exactly one authentication method.",
    )


def require_browser_credential(request: Request) -> SecretStr:
    if request.headers.getlist("Authorization"):
        raise invalid_credentials()
    credential = cookie_value(request, SESSION_COOKIE)
    if credential is None:
        raise browser_error(BrowserSessionInvalid())
    return credential


def verify_site(request: Request, *, mutation: bool = False) -> None:
    origins = request.headers.getlist("Origin")
    if (mutation and len(origins) != 1) or (
        origins and origins != [settings_for(request).web_origin]
    ):
        raise ApiError(
            status_code=403,
            code="browser_origin_forbidden",
            message="This request must come from the application.",
        )
    sites = request.headers.getlist("Sec-Fetch-Site")
    if len(sites) > 1 or (sites and sites[0] not in {"same-origin", "same-site", "none"}):
        raise ApiError(
            status_code=403,
            code="browser_origin_forbidden",
            message="This request must come from the application.",
        )


def verify_context(request: Request, credential: SecretStr, *, mutation: bool = False) -> str:
    verify_site(request, mutation=mutation)
    versions = request.headers.getlist(CONTEXT_HEADER)
    if len(versions) != 1 or not re.fullmatch(r"[0-9a-f]{32}\.[1-9][0-9]{0,18}", versions[0]):
        raise browser_error(BrowserContextStale())
    if mutation:
        values = request.headers.getlist(CSRF_HEADER)
        if (
            len(values) != 1
            or not re.fullmatch(r"[0-9a-f]{64}", values[0])
            or not hmac.compare_digest(values[0], csrf_token(credential))
        ):
            raise ApiError(
                status_code=403,
                code="browser_csrf_invalid",
                message="The session request could not be verified.",
            )
    return versions[0]


async def resolve_browser_principal(request: Request, credential: SecretStr) -> PrincipalContext:
    service = service_for(request)
    try:
        version = verify_context(
            request, credential, mutation=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        # Import locally: the demo HTTP adapter reuses the cookie/CSRF helpers above.
        from enterprise_doc_api.browser_auth.demo import allow_demo_operation, demo_service

        demo = demo_service(request)
        guest = await demo.get(credential, version) if demo is not None else None
        if guest is not None:
            allow_demo_operation(request.method, request.url.path)
            principal = guest.principal
        else:
            principal = await service.authorize(credential=credential, context_version=version)
    except BrowserSessionError as error:
        raise browser_error(error) from None
    request.state.browser_credential = credential
    request.state.browser_context_version = version
    enrich_request_principal(principal)
    return principal


def browser_error(error: BrowserSessionError) -> ApiError:
    status_code = 503
    if isinstance(error, BrowserSessionInvalid):
        status_code = 401
    elif isinstance(error, BrowserPrincipalForbidden):
        status_code = 403
    elif isinstance(error, (BrowserContextStale, BrowserIdentityConflict, BrowserSessionBusy)):
        status_code = 409
    elif isinstance(error, BrowserLoginInvalid):
        status_code = 400
    elif isinstance(error, BrowserLoginRateLimited):
        status_code = 429
    return ApiError(
        status_code=status_code,
        code=error.code,
        message="The browser session request could not be completed.",
        headers={"Retry-After": "60"} if status_code == 429 else None,
    )


class BrowserResponseSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        protected = scope["type"] == "http" and str(scope.get("path", "")).startswith(
            ("/auth/", "/api/")
        )

        async def secure_send(message: Message) -> None:
            if protected and message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"
                if str(scope.get("path", "")).startswith("/auth/"):
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
                    )
            await send(message)

        await self.app(scope, receive, secure_send)
