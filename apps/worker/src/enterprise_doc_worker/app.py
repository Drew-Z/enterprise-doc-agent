from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from enterprise_doc_core.health import (
    ComponentStatus,
    HealthChecker,
    OverallStatus,
    ReadinessResponse,
    StaticChecker,
    build_foundation_resources,
    evaluate_readiness,
)
from enterprise_doc_core.telemetry import MetricsRuntime, instrument_health_checkers
from enterprise_doc_worker.config import WorkerSettings


class LivenessResponse(BaseModel):
    status: str = "alive"


def default_checkers() -> list[HealthChecker]:
    return [
        StaticChecker("database", ComponentStatus.DOWN),
        StaticChecker("redis", ComponentStatus.DOWN),
        StaticChecker("object_store", ComponentStatus.DOWN),
    ]


def create_probe_app(
    *,
    settings: WorkerSettings | None = None,
    checkers: Sequence[HealthChecker] | None = None,
    readiness_timeout_seconds: float | None = None,
    metrics: MetricsRuntime | None = None,
) -> FastAPI:
    resolved_settings = settings or WorkerSettings()
    resolved_metrics = metrics if metrics is not None else MetricsRuntime.create()
    if checkers is None:
        resources = build_foundation_resources(resolved_settings)
        resolved_checkers = resources.checkers
    else:
        resources = None
        resolved_checkers = tuple(checkers)
    if resolved_settings.otel.metrics_enabled:
        resolved_checkers = instrument_health_checkers(resolved_checkers, resolved_metrics)
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
        title="Enterprise Document Agent Worker Probes",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.metrics = resolved_metrics

    if resolved_settings.otel.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint() -> Response:
            return Response(
                content=resolved_metrics.render(),
                headers={"Content-Type": resolved_metrics.content_type},
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
