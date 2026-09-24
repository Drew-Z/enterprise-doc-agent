import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.documents.embedding_provider import build_embedding_provider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway, PresalesGateway
from enterprise_doc_core.presales.generation import GenerationService
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.config import WorkerSettings


async def run_presales(
    settings: WorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    shutdown: asyncio.Event,
    metrics: MetricsRuntime,
) -> None:
    provider, model, dimension = build_embedding_provider(settings.embedding)
    routes = [settings.presales.model_route]
    if settings.presales.automatic_failover_enabled:
        routes.append("fallback" if routes[0] == "primary" else "primary")
    gateways: dict[str, PresalesGateway] = {
        route: OpenAICompatiblePresalesGateway(
            settings.model,
            presales_settings=settings.presales.model_copy(update={"model_route": route}),
        )
        for route in routes
    }
    generation = GenerationService(
        sessions,
        HybridRetrievalService(
            session_factory=sessions,
            embedding_provider=provider,
            embedding_model=model,
            embedding_dimension=dimension,
            query_instruction=settings.embedding.query_instruction,
            require_vector_evidence=settings.retrieval.require_vector_evidence,
            metrics=metrics,
        ),
        gateways[settings.presales.model_route],
        settings.presales,
        lambda: datetime.now(UTC),
        EntitlementUsageService(session_factory=sessions, app_env=settings.app_env),
    )
    worker = BackgroundGeneration(generation, gateways)
    worker_id = f"{settings.worker.worker_id[:140]}-presales-{uuid4().hex}"
    while not shutdown.is_set():
        if not await worker.run_once(worker_id):
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1)
            except TimeoutError:
                pass
