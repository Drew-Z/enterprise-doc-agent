from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
)
from enterprise_doc_core.jobs import JobRuntimeService, RetryDisposition
from enterprise_doc_core.object_store import MultipartObjectStore
from enterprise_doc_worker.queue import JobDeliveryConsumer


def classify_ingestion_error(error: Exception) -> RetryDisposition:
    if isinstance(error, DocumentIngestionError) and not error.retryable:
        return RetryDisposition.PERMANENT
    return RetryDisposition.RETRYABLE


def build_consumer_factory(
    *,
    runtime: JobRuntimeService,
    session_factory: async_sessionmaker[AsyncSession],
    object_store: MultipartObjectStore,
    documents_bucket: str,
    worker_id: str,
) -> Callable[[], JobDeliveryConsumer]:
    service = DocumentIngestionService(
        session_factory=session_factory,
        object_store=object_store,
        documents_bucket=documents_bucket,
        embedding_provider=HashEmbeddingProvider(),
    )

    def factory() -> JobDeliveryConsumer:
        return JobDeliveryConsumer(
            runtime=runtime,
            worker_id=worker_id,
            handler=service,
            classify_error=classify_ingestion_error,
        )

    return factory
