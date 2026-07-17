from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import Span
from pydantic import BaseModel

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.middleware import RequestContextMiddleware
from enterprise_doc_core.health import (
    ComponentStatus,
    HealthChecker,
    OverallStatus,
    ReadinessResponse,
    StaticChecker,
    build_foundation_resources,
    evaluate_readiness,
)
from enterprise_doc_core.telemetry import TelemetryRuntime


class LivenessResponse(BaseModel):
    status: str = "alive"


def _sanitize_request_span(span: Span, scope: dict[str, Any]) -> None:
    if not span.is_recording():
        return

    scheme = str(scope.get("scheme", "http"))
    server = scope.get("server")
    if isinstance(server, tuple) and len(server) == 2:
        authority = f"{server[0]}:{server[1]}"
    else:
        authority = "unknown"
    path = f"{scope.get('root_path', '')}{scope.get('path', '/')}"
    sanitized_url = f"{scheme}://{authority}{path}"
    span.set_attribute("http.url", sanitized_url)
    span.set_attribute("url.full", sanitized_url)


def default_checkers() -> list[HealthChecker]:
    return [
        StaticChecker("database", ComponentStatus.DOWN),
        StaticChecker("redis", ComponentStatus.DOWN),
        StaticChecker("object_store", ComponentStatus.DOWN),
    ]


def create_app(
    *,
    settings: ApiSettings | None = None,
    checkers: Sequence[HealthChecker] | None = None,
    readiness_timeout_seconds: float | None = None,
    telemetry: TelemetryRuntime | None = None,
) -> FastAPI:
    resolved_settings = settings or ApiSettings()
    if checkers is None:
        resources = build_foundation_resources(resolved_settings)
        resolved_checkers = resources.checkers
    else:
        resources = None
        resolved_checkers = tuple(checkers)
    timeout_seconds = readiness_timeout_seconds or max(
        resolved_settings.database.connect_timeout_seconds,
        resolved_settings.redis.connect_timeout_seconds,
        resolved_settings.object_store.connect_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if resources is not None:
                await resources.close()

    app = FastAPI(
        title="Enterprise Document Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.api.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["X-Request-ID", "X-Correlation-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    if telemetry is not None and telemetry.enabled and telemetry.provider is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=telemetry.provider,
            server_request_hook=_sanitize_request_span,
        )

    @app.get("/health/live", response_model=LivenessResponse)
    async def live() -> LivenessResponse:
        return LivenessResponse()

    @app.get(
        "/health/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    async def ready() -> ReadinessResponse | JSONResponse:
        result = await evaluate_readiness(
            resolved_checkers,
            timeout_seconds=timeout_seconds,
        )
        if result.status is OverallStatus.NOT_READY:
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        return result

    return app
