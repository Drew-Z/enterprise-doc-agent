from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from enterprise_doc_api.auth import resolve_request_principal
from enterprise_doc_api.errors import (
    ApiError,
    api_error_response,
    unexpected_error_response,
)

_LOGGER = logging.getLogger("enterprise_doc_api.auth")


class ApiAuthenticationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not str(scope.get("path", "")).startswith("/api/")
            or scope.get("method") == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        try:
            principal = await resolve_request_principal(request)
        except ApiError as error:
            _log_auth_failure(scope, error_code=error.code, error_type=type(error).__name__)
            await api_error_response(error)(scope, receive, send)
            return
        except Exception as error:
            _log_auth_failure(
                scope,
                error_code="auth_internal_error",
                error_type=type(error).__name__,
            )
            await unexpected_error_response(error)(scope, receive, send)
            return

        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def _log_auth_failure(scope: Scope, *, error_code: str, error_type: str) -> None:
    """Emit a bounded security signal without logging credentials or claim data."""
    _LOGGER.warning(
        "auth_failed",
        extra={
            "event_data": {
                "method": scope.get("method"),
                "surface": "api",
                "error_code": error_code,
                "error_type": error_type,
            }
        },
    )
