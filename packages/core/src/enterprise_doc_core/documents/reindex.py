from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import exists, select

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.documents.embedding_provider import embedding_model_identity
from enterprise_doc_core.documents.models import (
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.jobs import create_job_records


async def enqueue_reindex(
    settings: FoundationSettings,
    *,
    apply: bool,
    tenant_id: UUID | None,
    limit: int,
) -> dict[str, object]:
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    model_identity = embedding_model_identity(settings.embedding)
    target_exists = exists(
        select(DocumentIngestionGeneration.id).where(
            DocumentIngestionGeneration.document_version_id == DocumentVersion.id,
            DocumentIngestionGeneration.embedding_version == settings.embedding.version,
            DocumentIngestionGeneration.embedding_model == model_identity,
            DocumentIngestionGeneration.embedding_dimension == settings.embedding.dimension,
            DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
            DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
            DocumentIngestionGeneration.active.is_(True),
        )
    )
    statement = (
        select(DocumentVersion)
        .where(
            DocumentVersion.status == DocumentVersionStatus.READY.value,
            ~target_exists,
        )
        .order_by(DocumentVersion.created_at, DocumentVersion.id)
        .limit(limit)
    )
    if tenant_id is not None:
        statement = statement.where(DocumentVersion.tenant_id == tenant_id)

    created = 0
    replayed = 0
    versions: Sequence[DocumentVersion] = ()
    try:
        async with session_factory.begin() as session:
            versions = (await session.scalars(statement)).all()
            if apply:
                for version in versions:
                    result = await create_job_records(
                        session,
                        tenant_id=version.tenant_id,
                        actor_id=version.created_by,
                        job_type="document.ingest",
                        idempotency_key=(
                            f"embedding-v{settings.embedding.version}:{version.id}"
                        ),
                        payload={"document_version_id": str(version.id)},
                        document_version_id=version.id,
                        outbox_event_type="document.ingest.requested",
                    )
                    if result.replayed:
                        replayed += 1
                    else:
                        created += 1
    finally:
        await engine.dispose()

    return {
        "status": "applied" if apply else "planned",
        "selected": len(versions),
        "created": created,
        "replayed": replayed,
        "embedding_model": model_identity,
        "embedding_dimension": settings.embedding.dimension,
        "embedding_version": settings.embedding.version,
        "values_redacted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or enqueue reindex jobs for the configured embedding generation"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--tenant-id", type=UUID)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")

    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        report = runner.run(
            enqueue_reindex(
                FoundationSettings(),
                apply=args.apply,
                tenant_id=args.tenant_id,
                limit=args.limit,
            )
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
