from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select, update
from tests.jobs.test_m3_ingestion_integration import (
    FailOnceEmbeddingProvider,
    FakeObjectStore,
    _seed_uploaded_document,
)

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
)
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.uploads.models import UploadSession


@pytest.mark.integration
@pytest.mark.parametrize("legacy_checkpoint", [False, True])
async def test_declared_hash_mismatch_cannot_activate_new_or_unverified_checkpoint(
    legacy_checkpoint: bool,
) -> None:
    content = b"# Retention\nRetention is 30 days.\n"
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    tenant_id, actor_id, version_id, claim = await _seed_uploaded_document(
        sessions, content=content
    )
    store = FakeObjectStore(content)
    provider = FailOnceEmbeddingProvider()
    service = DocumentIngestionService(
        session_factory=sessions,
        object_store=store,
        documents_bucket="documents",
        embedding_provider=provider,
    )
    try:
        if legacy_checkpoint:
            with pytest.raises(DocumentIngestionError) as first:
                await service(claim)
            assert first.value.code == "ingestion_failed"
            async with sessions.begin() as session:
                version = await session.get(DocumentVersion, version_id)
                assert version is not None
                # Reproduce an embed checkpoint written before content verification.
                version.content_sha256_verified_at = None
        store.content = content.replace(b"30", b"90")
        calls_before = len(provider.calls)
        with pytest.raises(DocumentIngestionError) as rejected:
            await service(claim)
        assert rejected.value.code == "document_sha256_mismatch"
        assert rejected.value.retryable is False
        assert len(provider.calls) == calls_before
        assert store.head_calls == (2 if legacy_checkpoint else 1)
        async with sessions() as session:
            version = await session.get(DocumentVersion, version_id)
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_version_id == version_id)
            )
        assert version is not None and generation is not None
        assert version.status == "failed"
        assert version.content_sha256_verified_at is None
        assert generation.status == "failed" and generation.active is False
        assert generation.error_code == "document_sha256_mismatch"
        assert chunk_count == 0
    finally:
        async with sessions.begin() as session:
            await session.execute(
                update(UploadSession)
                .where(UploadSession.tenant_id == tenant_id)
                .values(document_version_id=None)
            )
            await session.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()
