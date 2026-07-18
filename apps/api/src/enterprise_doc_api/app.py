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
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_doc_api.auth import (
    DatabasePrincipalResolver,
    JwtTokenCodec,
    PrincipalResolver,
)
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.errors import register_error_handlers
from enterprise_doc_api.jobs import router as jobs_router
from enterprise_doc_api.middleware import ApiAuthenticationMiddleware, RequestContextMiddleware
from enterprise_doc_api.uploads import router as upload_router
from enterprise_doc_api.uploads.router import (
    UploadCreationServiceProtocol,
    UploadSessionServiceProtocol,
)
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.health import (
    ComponentStatus,
    HealthChecker,
    OverallStatus,
    ReadinessResponse,
    StaticChecker,
    build_foundation_resources,
    evaluate_readiness,
)
from enterprise_doc_core.jobs import JobRuntimeService
from enterprise_doc_core.object_store import (
    Boto3MultipartObjectStore,
    MultipartObjectStore,
)
from enterprise_doc_core.telemetry import TelemetryRuntime
from enterprise_doc_core.uploads import UploadCreationService, UploadSessionService


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
    principal_resolver: PrincipalResolver | None = None,
    upload_creation_service: UploadCreationServiceProtocol | None = None,
    upload_session_service: UploadSessionServiceProtocol | None = None,
) -> FastAPI:
    resolved_settings = settings if settings is not None else ApiSettings()
    if checkers is None:
        resources = build_foundation_resources(resolved_settings)
        resolved_checkers = resources.checkers
    else:
        resources = None
        resolved_checkers = tuple(checkers)
    needs_default_database = (
        principal_resolver is None
        or upload_creation_service is None
        or upload_session_service is None
    )
    needs_default_object_store = upload_creation_service is None or upload_session_service is None
    owned_database_engine: AsyncEngine | None = None
    owned_multipart_object_store: Boto3MultipartObjectStore | None = None
    if resources is not None:
        business_database_engine: AsyncEngine | None = resources.database_engine
        business_object_store: MultipartObjectStore | None = resources.multipart_object_store
    elif needs_default_database:
        owned_database_engine = create_database_engine(resolved_settings.database)
        business_database_engine = owned_database_engine
        if needs_default_object_store:
            owned_multipart_object_store = Boto3MultipartObjectStore(
                settings=resolved_settings.object_store
            )
            business_object_store = owned_multipart_object_store
        else:
            business_object_store = None
    else:
        business_database_engine = None
        business_object_store = None
    session_factory = (
        create_session_factory(business_database_engine)
        if business_database_engine is not None
        else None
    )
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
            else:
                if owned_database_engine is not None:
                    await owned_database_engine.dispose()
                if owned_multipart_object_store is not None:
                    await owned_multipart_object_store.close()

    app = FastAPI(
        title="Enterprise Document Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(ApiAuthenticationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.api.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Correlation-ID",
        ],
        expose_headers=["X-Request-ID", "X-Correlation-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.state.principal_resolver = (
        principal_resolver
        if principal_resolver is not None
        else DatabasePrincipalResolver(
            session_factory=session_factory,
            codec=JwtTokenCodec(resolved_settings.auth),
        )
    )
    app.state.upload_creation_service = (
        upload_creation_service
        if upload_creation_service is not None
        else UploadCreationService(
            session_factory=session_factory,
            settings=resolved_settings.upload,
            object_store=_required_object_store(business_object_store),
            documents_bucket=resolved_settings.object_store.documents_bucket,
        )
    )
    app.state.upload_session_service = (
        upload_session_service
        if upload_session_service is not None
        else UploadSessionService(
            session_factory=session_factory,
            object_store=_required_object_store(business_object_store),
            documents_bucket=resolved_settings.object_store.documents_bucket,
            settings=resolved_settings.upload,
        )
    )
    app.state.job_runtime_service = (
        JobRuntimeService(session_factory=session_factory) if session_factory is not None else None
    )
    app.include_router(upload_router)
    app.include_router(jobs_router)
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


def _required_object_store(
    object_store: MultipartObjectStore | None,
) -> MultipartObjectStore:
    if object_store is None:
        raise RuntimeError("default upload services require an object store")
    return object_store
