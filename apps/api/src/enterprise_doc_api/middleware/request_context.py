from __future__ import annotations

import logging
import re
from time import perf_counter
from uuid import uuid4

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from enterprise_doc_core.context import (
    RequestContext,
    reset_request_context,
    set_request_context,
)

_HEADER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOGGER = logging.getLogger("enterprise_doc_api.request")


def _resolve_identifier(value: str | None) -> str:
    if value is not None and _HEADER_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = _resolve_identifier(_decode_header(headers.get(b"x-request-id")))
        correlation_id = _resolve_identifier(_decode_header(headers.get(b"x-correlation-id")))
        token = set_request_context(
            RequestContext(request_id=request_id, correlation_id=correlation_id)
        )
        started = perf_counter()
        status_code = 500
        span = trace.get_current_span()
        span.set_attribute("app.request_id", request_id)
        span.set_attribute("app.correlation_id", correlation_id)

        async def send_with_context(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-correlation-id", correlation_id.encode("ascii")),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception as error:
            _LOGGER.error(
                "request_failed",
                extra={
                    "event_data": {
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "error_type": type(error).__name__,
                    }
                },
            )
            raise
        finally:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            _LOGGER.info(
                "request_completed",
                extra={
                    "event_data": {
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    }
                },
            )
            reset_request_context(token)


def _decode_header(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        return None
