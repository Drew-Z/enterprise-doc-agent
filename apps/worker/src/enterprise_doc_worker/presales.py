import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.documents.embedding_provider import managed_embedding_provider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway, PresalesGateway
from enterprise_doc_core.presales.generation import GenerationService
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.lifecycle import WorkerProgress


async def run_presales_pool(
    worker: BackgroundGeneration,
    shutdown: asyncio.Event,
    *,
    worker_id: str,
    concurrency: int = 1,
    progress: WorkerProgress | None = None,
    progress_timeout_seconds: float = 420,
) -> None:
    """Run a fixed number of claim loops, without prefetching queued work."""
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError("invalid_presales_concurrency")
    if shutdown.is_set():
        return
    completed = [
        progress.register(f"presales.{slot}", timeout_seconds=progress_timeout_seconds)
        if progress is not None
        else None
        for slot in range(concurrency)
    ]

    async def poll(slot: int) -> None:
        while not shutdown.is_set():
            worked = await worker.run_once(f"{worker_id}-{slot}", include_demo=slot == 0)
            callback = completed[slot]
            if callback is not None:
                callback()
            if not worked:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=1)
                except TimeoutError:
                    pass

    lanes = [
        asyncio.create_task(poll(slot), name=f"worker.presales.{slot}")
        for slot in range(concurrency)
    ]
    stopped = asyncio.create_task(shutdown.wait(), name="worker.presales.shutdown")
    try:
        done, _ = await asyncio.wait([*lanes, stopped], return_when=asyncio.FIRST_COMPLETED)
        for lane in lanes:
            if lane in done:
                await lane
    finally:
        for task in [*lanes, stopped]:
            task.cancel()
        await asyncio.gather(*lanes, stopped, return_exceptions=True)


async def run_presales(
    settings: WorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    shutdown: asyncio.Event,
    metrics: MetricsRuntime,
    *,
    progress: WorkerProgress | None = None,
) -> None:
    async with managed_embedding_provider(settings.embedding, app_env=settings.app_env) as (
        provider,
        model,
        dimension,
    ):
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
                app_env=settings.app_env,
                provider_usage_settings=settings.provider_usage,
            ),
            gateways[settings.presales.model_route],
            settings.presales,
            lambda: datetime.now(UTC),
            EntitlementUsageService(session_factory=sessions, app_env=settings.app_env),
        )
        worker = BackgroundGeneration(generation, gateways)
        worker_id = f"{settings.worker.worker_id[:140]}-presales-{uuid4().hex}"
        await run_presales_pool(
            worker,
            shutdown,
            worker_id=worker_id,
            concurrency=settings.worker.presales_concurrency,
            progress=progress,
            progress_timeout_seconds=settings.presales.row_timeout_seconds
            + 2 * settings.database.pool_timeout_seconds
            + 30,
        )
