from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import EmbeddingSettings, FaultInjectionSettings
from enterprise_doc_core.documents import build_embedding_provider
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
    IngestionVersions,
)
from enterprise_doc_core.jobs import ClaimedJob, JobRuntimeService, RetryDisposition
from enterprise_doc_core.object_store import MultipartObjectStore
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.agent_handler import AGENT_EXECUTE_JOB_TYPE
from enterprise_doc_worker.faults import wrap_handler, wrap_multipart_store
from enterprise_doc_worker.queue import (
    AsyncJobHandler,
    JobDeliveryConsumer,
    JobHandlerError,
)

DOCUMENT_INGEST_JOB_TYPE = "document.ingest"


class UnsupportedJobType(JobHandlerError):
    code = "unsupported_job_type"
    message = "The claimed job type is not supported by this worker."


class JobHandlerRouter:
    def __init__(self, handlers: Mapping[str, AsyncJobHandler]) -> None:
        self.handlers = dict(handlers)

    async def __call__(self, claim: ClaimedJob) -> None:
        handler = self.handlers.get(claim.job_type)
        if handler is None:
            raise UnsupportedJobType()
        await handler(claim)


def classify_job_error(error: Exception) -> RetryDisposition:
    if isinstance(error, (DocumentIngestionError, JobHandlerError)) and not error.retryable:
        return RetryDisposition.PERMANENT
    return RetryDisposition.RETRYABLE


def build_consumer_factory(
    *,
    runtime: JobRuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    object_store: MultipartObjectStore,
    documents_bucket: str,
    worker_id: str,
    agent_handler: AsyncJobHandler | None = None,
    metrics: MetricsRuntime | None = None,
    fault_injection: FaultInjectionSettings | None = None,
    embedding_settings: EmbeddingSettings | None = None,
) -> Callable[[], JobDeliveryConsumer]:
    resolved_faults = fault_injection or FaultInjectionSettings()
    resolved_embedding = embedding_settings or EmbeddingSettings()
    embedding_provider, embedding_model, embedding_dimension = build_embedding_provider(
        resolved_embedding
    )
    service = DocumentIngestionService(
        session_factory=session_factory,
        object_store=wrap_multipart_store(object_store, resolved_faults),
        documents_bucket=documents_bucket,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        versions=IngestionVersions(embedding=resolved_embedding.version),
        metrics=metrics,
    )

    handlers: dict[str, AsyncJobHandler] = {DOCUMENT_INGEST_JOB_TYPE: service}
    if agent_handler is not None:
        handlers[AGENT_EXECUTE_JOB_TYPE] = agent_handler
    router: AsyncJobHandler = wrap_handler(JobHandlerRouter(handlers), resolved_faults)

    def factory() -> JobDeliveryConsumer:
        return JobDeliveryConsumer(
            runtime=runtime,
            worker_id=worker_id,
            handler=router,
            classify_error=classify_job_error,
            metrics=metrics,
        )

    return factory
