from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from enterprise_doc_api.auth import resolve_request_principal
from enterprise_doc_api.errors import (
    ApiError,
    api_error_response,
    unexpected_error_response,
)


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
            await api_error_response(error)(scope, receive, send)
            return
        except Exception as error:
            await unexpected_error_response(error)(scope, receive, send)
            return

        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)
