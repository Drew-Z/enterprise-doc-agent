from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import Span
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from enterprise_doc_api.agents import router as agent_router
from enterprise_doc_api.agents.router import AgentRunServiceProtocol
from enterprise_doc_api.approvals import router as approval_router
from enterprise_doc_api.approvals.router import ApprovalServiceProtocol
from enterprise_doc_api.artifacts import router as artifact_router
from enterprise_doc_api.artifacts.router import AgentArtifactServiceProtocol
from enterprise_doc_api.audit import router as audit_router
from enterprise_doc_api.audit.router import (
    AuditEventServiceProtocol,
    AuditGovernanceServiceProtocol,
    governance_router,
)
from enterprise_doc_api.auth import (
    DatabaseExternalMembershipResolver,
    DatabasePrincipalResolver,
    ExternalPrincipalResolver,
    JwksExternalIdentityAdapter,
    JwtTokenCodec,
    PrincipalResolver,
)
from enterprise_doc_api.auth.session_router import (
    LocalTokenRevocationServiceProtocol,
)
from enterprise_doc_api.auth.session_router import (
    router as session_router,
)
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.documents import router as document_router
from enterprise_doc_api.documents.router import (
    DocumentInventoryServiceProtocol,
    DocumentPolicyServiceProtocol,
)
from enterprise_doc_api.errors import register_error_handlers
from enterprise_doc_api.identity import router as identity_router
from enterprise_doc_api.identity.members_router import (
    MembershipAdministrationServiceProtocol,
)
from enterprise_doc_api.identity.members_router import (
    router as members_router,
)
from enterprise_doc_api.identity.router import ExternalIdentityBindingServiceProtocol
from enterprise_doc_api.identity.scim_router import (
    ScimProvisioningServiceProtocol,
)
from enterprise_doc_api.identity.scim_router import (
    discovery_router as scim_discovery_router,
)
from enterprise_doc_api.identity.scim_router import (
    router as scim_router,
)
from enterprise_doc_api.jobs import router as jobs_router
from enterprise_doc_api.middleware import (
    ApiAuthenticationMiddleware,
    MetricsMiddleware,
    RequestContextMiddleware,
)
from enterprise_doc_api.uploads import router as upload_router
from enterprise_doc_api.uploads.router import (
    UploadCreationServiceProtocol,
    UploadSessionServiceProtocol,
)
from enterprise_doc_core.agents import AgentArtifactService, AgentRunService, ApprovalService
from enterprise_doc_core.audit import AuditEventService, AuditGovernanceService
from enterprise_doc_core.auth import LocalTokenRevocationService
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import DocumentInventoryService, DocumentPolicyService
from enterprise_doc_core.health import (
    ComponentStatus,
    HealthChecker,
    OverallStatus,
    ReadinessCache,
    ReadinessResponse,
    StaticChecker,
    build_foundation_resources,
)
from enterprise_doc_core.identity.membership_service import (
    MembershipAdministrationService,
)
from enterprise_doc_core.identity.scim_service import ScimProvisioningService
from enterprise_doc_core.identity.service import ExternalIdentityBindingService
from enterprise_doc_core.jobs import JobRuntimeService
from enterprise_doc_core.object_store import (
    Boto3ArtifactObjectStore,
    Boto3MultipartObjectStore,
    MultipartObjectStore,
)
from enterprise_doc_core.telemetry import (
    MetricsRuntime,
    TelemetryRuntime,
    instrument_health_checkers,
)
from enterprise_doc_core.uploads import UploadCreationService, UploadSessionService


class LivenessResponse(BaseModel):
    status: str = "alive"


def _required_session_factory(
    value: async_sessionmaker[AsyncSession] | None,
) -> async_sessionmaker[AsyncSession]:
    if value is None:
        raise RuntimeError("database session factory is required")
    return value


def _sanitize_request_span(span: Span, scope: dict[str, Any]) -> None:
    if not span.is_recording():
        return

    scheme = str(scope.get("scheme", "http"))
    server = scope.get("server")
    if isinstance(server, tuple) and len(server) == 2:
        authority = f"{server[0]}:{server[1]}"
    else:
        authority = "unknown"
    # Keep trace URLs free of document, run, tenant, and user identifiers.
    sanitized_url = f"{scheme}://{authority}"
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
    readiness_cache_ttl_seconds: float | None = None,
    telemetry: TelemetryRuntime | None = None,
    principal_resolver: PrincipalResolver | None = None,
    external_principal_resolver: PrincipalResolver | None = None,
    upload_creation_service: UploadCreationServiceProtocol | None = None,
    upload_session_service: UploadSessionServiceProtocol | None = None,
    document_inventory_service: DocumentInventoryServiceProtocol | None = None,
    document_policy_service: DocumentPolicyServiceProtocol | None = None,
    agent_run_service: AgentRunServiceProtocol | None = None,
    approval_service: ApprovalServiceProtocol | None = None,
    agent_artifact_service: AgentArtifactServiceProtocol | None = None,
    audit_service: AuditEventServiceProtocol | None = None,
    audit_governance_service: AuditGovernanceServiceProtocol | None = None,
    external_identity_binding_service: ExternalIdentityBindingServiceProtocol | None = None,
    membership_administration_service: MembershipAdministrationServiceProtocol | None = None,
    scim_provisioning_service: ScimProvisioningServiceProtocol | None = None,
    token_revocation_service: LocalTokenRevocationServiceProtocol | None = None,
    metrics: MetricsRuntime | None = None,
) -> FastAPI:
    resolved_settings = settings if settings is not None else ApiSettings()
    if (
        resolved_settings.auth.external_auth_enabled
        and external_principal_resolver is None
        and not resolved_settings.auth.external_jwks_url
    ):
        raise RuntimeError(
            "external authentication has no external principal resolver; "
            "configure a JWKS URL or inject one"
        )
    resolved_principal_resolver = (
        external_principal_resolver
        if resolved_settings.auth.external_auth_enabled
        else principal_resolver
    )
    resolved_metrics = metrics if metrics is not None else MetricsRuntime.create()
    if checkers is None:
        resources = build_foundation_resources(resolved_settings, metrics=resolved_metrics)
        resolved_checkers = resources.checkers
    else:
        resources = None
        resolved_checkers = tuple(checkers)
    if resolved_settings.otel.metrics_enabled:
        resolved_checkers = instrument_health_checkers(resolved_checkers, resolved_metrics)
    needs_default_database = (
        resolved_principal_resolver is None
        or upload_creation_service is None
        or upload_session_service is None
        or document_inventory_service is None
        or document_policy_service is None
        or agent_run_service is None
        or approval_service is None
        or agent_artifact_service is None
        or audit_service is None
        or audit_governance_service is None
        or external_identity_binding_service is None
        or membership_administration_service is None
        or (resolved_settings.auth.scim_enabled and scim_provisioning_service is None)
        or token_revocation_service is None
    )
    needs_default_object_store = upload_creation_service is None or upload_session_service is None
    owned_database_engine: AsyncEngine | None = None
    owned_multipart_object_store: Boto3MultipartObjectStore | None = None
    owned_artifact_object_store: Boto3ArtifactObjectStore | None = None
    if resources is not None:
        business_database_engine: AsyncEngine | None = resources.database_engine
        business_object_store: MultipartObjectStore | None = resources.multipart_object_store
    elif needs_default_database:
        owned_database_engine = create_database_engine(
            resolved_settings.database,
            metrics=resolved_metrics,
        )
        business_database_engine = owned_database_engine
        if needs_default_object_store:
            owned_multipart_object_store = Boto3MultipartObjectStore(
                settings=resolved_settings.object_store,
                metrics=resolved_metrics,
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
    if (
        resolved_settings.auth.external_auth_enabled
        and resolved_principal_resolver is None
        and resolved_settings.auth.external_jwks_url
    ):
        external_membership_resolver = DatabaseExternalMembershipResolver(
            session_factory=session_factory,
        )
        resolved_principal_resolver = ExternalPrincipalResolver(
            adapter=JwksExternalIdentityAdapter(settings=resolved_settings.auth),
            settings=resolved_settings.auth,
            membership_resolver=external_membership_resolver,
            identity_binding_resolver=external_membership_resolver,
        )
    timeout_seconds = readiness_timeout_seconds or max(
        resolved_settings.database.connect_timeout_seconds,
        resolved_settings.redis.connect_timeout_seconds,
        resolved_settings.object_store.connect_timeout_seconds,
    )
    readiness_cache = ReadinessCache(
        resolved_checkers,
        timeout_seconds=timeout_seconds,
        ttl_seconds=(
            resolved_settings.api.readiness_cache_ttl_seconds
            if readiness_cache_ttl_seconds is None
            else readiness_cache_ttl_seconds
        ),
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
            if owned_artifact_object_store is not None:
                await owned_artifact_object_store.close()

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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
            "X-Request-ID",
            "X-Correlation-ID",
        ],
        expose_headers=["X-Request-ID", "X-Correlation-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        MetricsMiddleware,
        metrics=resolved_metrics,
        enabled=resolved_settings.otel.metrics_enabled,
    )
    register_error_handlers(app)
    app.state.principal_resolver = (
        resolved_principal_resolver
        if resolved_principal_resolver is not None
        else DatabasePrincipalResolver(
            session_factory=session_factory,
            codec=JwtTokenCodec(resolved_settings.auth),
        )
    )
    app.state.auth_settings = resolved_settings.auth
    app.state.metrics = resolved_metrics
    app.state.readiness_cache = readiness_cache
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
            ingestion_max_attempts=resolved_settings.embedding.ingestion_max_attempts,
            checksum_mode=resolved_settings.object_store.multipart_checksum_mode,
        )
    )
    app.state.job_runtime_service = (
        JobRuntimeService(session_factory=session_factory) if session_factory is not None else None
    )
    app.state.document_inventory_service = (
        document_inventory_service
        if document_inventory_service is not None
        else DocumentInventoryService(
            session_factory=_required_session_factory(session_factory),
        )
        if session_factory is not None
        else None
    )
    app.state.document_policy_service = (
        document_policy_service
        if document_policy_service is not None
        else DocumentPolicyService(
            session_factory=_required_session_factory(session_factory),
        )
        if session_factory is not None
        else None
    )
    app.state.agent_run_service = (
        agent_run_service
        if agent_run_service is not None
        else AgentRunService(
            session_factory=_required_session_factory(session_factory),
            agent_settings=resolved_settings.agent,
            model_settings=resolved_settings.model,
        )
    )
    app.state.approval_service = (
        approval_service
        if approval_service is not None
        else ApprovalService(
            session_factory=_required_session_factory(session_factory),
            resume_max_attempts=resolved_settings.agent.execution_max_attempts,
            metrics=resolved_metrics,
        )
    )
    if agent_artifact_service is None:
        owned_artifact_object_store = Boto3ArtifactObjectStore(
            settings=resolved_settings.object_store,
            metrics=resolved_metrics,
        )
        app.state.agent_artifact_service = AgentArtifactService(
            session_factory=_required_session_factory(session_factory),
            artifact_store=owned_artifact_object_store,
            metrics=resolved_metrics,
        )
    else:
        app.state.agent_artifact_service = agent_artifact_service
    audit_archive_store = owned_artifact_object_store
    if audit_archive_store is None and agent_artifact_service is not None:
        audit_archive_store = getattr(agent_artifact_service, "artifact_store", None)
    app.state.audit_service = (
        audit_service
        if audit_service is not None
        else AuditEventService(session_factory=_required_session_factory(session_factory))
        if session_factory is not None
        else None
    )
    app.state.audit_governance_service = (
        audit_governance_service
        if audit_governance_service is not None
        else AuditGovernanceService(
            session_factory=_required_session_factory(session_factory),
            archive_store=audit_archive_store,
            archive_bucket=resolved_settings.object_store.artifacts_bucket,
        )
        if session_factory is not None
        else None
    )
    app.state.external_identity_binding_service = (
        external_identity_binding_service
        if external_identity_binding_service is not None
        else ExternalIdentityBindingService(
            session_factory=_required_session_factory(session_factory),
        )
        if session_factory is not None
        else None
    )
    app.state.membership_administration_service = (
        membership_administration_service
        if membership_administration_service is not None
        else MembershipAdministrationService(
            session_factory=_required_session_factory(session_factory),
        )
        if session_factory is not None
        else None
    )
    app.state.scim_provisioning_service = (
        scim_provisioning_service
        if scim_provisioning_service is not None
        else ScimProvisioningService(
            session_factory=_required_session_factory(session_factory),
        )
        if resolved_settings.auth.scim_enabled and session_factory is not None
        else None
    )
    app.state.token_revocation_service = (
        token_revocation_service
        if token_revocation_service is not None
        else LocalTokenRevocationService(
            session_factory=_required_session_factory(session_factory),
        )
        if session_factory is not None
        else None
    )
    app.include_router(upload_router)
    app.include_router(document_router)
    app.include_router(jobs_router)
    app.include_router(agent_router)
    app.include_router(approval_router)
    app.include_router(artifact_router)
    app.include_router(audit_router)
    app.include_router(governance_router)
    app.include_router(session_router)
    app.include_router(identity_router)
    app.include_router(members_router)
    app.include_router(scim_router)
    app.include_router(scim_discovery_router)
    if resolved_settings.otel.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint() -> Response:
            return Response(
                content=resolved_metrics.render(),
                headers={"Content-Type": resolved_metrics.content_type},
            )

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
    async def ready(response: Response) -> ReadinessResponse | JSONResponse:
        result = await readiness_cache.get()
        response.headers["Cache-Control"] = "no-store"
        if result.status is OverallStatus.NOT_READY:
            return JSONResponse(
                status_code=503,
                content=result.model_dump(mode="json"),
                headers={"Cache-Control": "no-store"},
            )
        return result

    return app


def _required_object_store(
    object_store: MultipartObjectStore | None,
) -> MultipartObjectStore:
    if object_store is None:
        raise RuntimeError("default upload services require an object store")
    return object_store
