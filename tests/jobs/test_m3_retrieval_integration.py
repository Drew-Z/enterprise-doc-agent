from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.models import (
    DEFAULT_EMBEDDING_DIMENSION,
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus

VECTOR_A = (1.0,) + (0.0,) * (DEFAULT_EMBEDDING_DIMENSION - 1)
VECTOR_B = (0.0, 1.0) + (0.0,) * (DEFAULT_EMBEDDING_DIMENSION - 2)


class ControlledEmbeddingProvider:
    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.vectors[text] for text in texts)


@dataclass(frozen=True, slots=True)
class SeededVersion:
    tenant_id: UUID
    actor_id: UUID
    document_version_id: UUID
    filename: str


async def _seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    actor_id = uuid4()
    suffix = uuid4().hex
    async with session_factory.begin() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"M3 retrieval tenant {suffix}",
                slug=f"m3-retrieval-{suffix}",
                quota_bytes=1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"m3-retrieval-{suffix}@example.test"))
        await session.flush()
        session.add(
            Membership(
                tenant_id=tenant_id,
                user_id=actor_id,
                role=MembershipRole.OWNER.value,
                is_active=True,
            )
        )
    return tenant_id, actor_id


async def _seed_version(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: UUID,
    actor_id: UUID,
    filename: str,
) -> SeededVersion:
    document_id = uuid4()
    version_id = uuid4()
    upload_id = uuid4()
    suffix = uuid4().hex
    content = filename.encode("utf-8")
    sha256 = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        session.add(
            UploadSession(
                id=upload_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                pending_document_id=document_id,
                pending_version_id=version_id,
                status=UploadSessionStatus.COMPLETED.value,
                idempotency_key=f"retrieval-upload:{suffix}",
                request_fingerprint=sha256,
                object_key=f"{tenant_id}/documents/{version_id}/{filename}",
                original_filename=filename,
                extension=".txt",
                declared_media_type="text/plain",
                size_bytes=len(content),
                declared_sha256=sha256,
                part_size_bytes=len(content),
                expected_part_count=1,
                reserved_bytes=0,
                expires_at=now + timedelta(hours=1),
                completed_at=now,
            )
        )
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                created_by=actor_id,
                title=filename,
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                tenant_id=tenant_id,
                document_id=document_id,
                upload_session_id=upload_id,
                version_number=1,
                status=DocumentVersionStatus.READY.value,
                object_key=f"{tenant_id}/documents/{version_id}/{filename}",
                original_filename=filename,
                declared_media_type="text/plain",
                detected_media_type="text/plain",
                size_bytes=len(content),
                declared_sha256=sha256,
                created_by=actor_id,
            )
        )
        await session.flush()
        upload = await session.get(UploadSession, upload_id)
        assert upload is not None
        upload.document_version_id = version_id
    return SeededVersion(tenant_id, actor_id, version_id, filename)


async def _add_generation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    version: SeededVersion,
    text: str,
    embedding: tuple[float, ...],
    embedding_version: int = 1,
    active: bool = True,
    status: DocumentIngestionStatus = DocumentIngestionStatus.SUCCEEDED,
    stage: DocumentIngestionStage = DocumentIngestionStage.READY,
) -> tuple[UUID, UUID]:
    generation_id = uuid4()
    chunk_id = uuid4()
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        session.add(
            DocumentIngestionGeneration(
                id=generation_id,
                tenant_id=version.tenant_id,
                document_version_id=version.document_version_id,
                parser_version=1,
                chunker_version=1,
                embedding_version=embedding_version,
                embedding_model="controlled",
                embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
                status=status.value,
                stage=stage.value,
                chunk_count=1,
                embedded_count=1,
                active=active,
                started_at=now,
                finished_at=now,
            )
        )
        await session.flush()
        session.add(
            DocumentChunk(
                id=chunk_id,
                tenant_id=version.tenant_id,
                document_version_id=version.document_version_id,
                generation_id=generation_id,
                chunk_index=0,
                heading=None,
                page_number=None,
                start_offset=0,
                end_offset=len(text),
                normalized_text=text,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                search_vector=func.to_tsvector("simple", text),
                embedding=list(embedding),
            )
        )
    return generation_id, chunk_id


@pytest.mark.integration
async def test_keyword_and_vector_recall_work_independently() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    keyword_version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="keyword.txt",
    )
    vector_version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="vector.txt",
    )
    _, keyword_chunk_id = await _add_generation(
        session_factory,
        version=keyword_version,
        text="escrow account funding condition",
        embedding=VECTOR_A,
    )
    _, vector_chunk_id = await _add_generation(
        session_factory,
        version=vector_version,
        text="signed handover receipt",
        embedding=VECTOR_A,
    )
    provider = ControlledEmbeddingProvider(
        {
            "escrow": VECTOR_B,
            "proof of delivery": VECTOR_A,
        }
    )
    service = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=provider,
        top_k=5,
    )
    try:
        keyword_candidates = await service._keyword_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=keyword_version.document_version_id,
            query="escrow",
        )
        keyword_vector_candidates = await service._vector_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=keyword_version.document_version_id,
            vector=VECTOR_B,
        )
        assert [candidate.chunk_id for candidate in keyword_candidates] == [keyword_chunk_id]
        assert keyword_vector_candidates == ()
        assert (
            await service.retrieve(
                tenant_id=tenant_id,
                document_version_id=keyword_version.document_version_id,
                query="escrow",
            )
        ).accepted is True

        vector_keyword_candidates = await service._keyword_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=vector_version.document_version_id,
            query="proof of delivery",
        )
        vector_candidates = await service._vector_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=vector_version.document_version_id,
            vector=VECTOR_A,
        )
        assert vector_keyword_candidates == ()
        assert [candidate.chunk_id for candidate in vector_candidates] == [vector_chunk_id]
        assert (
            await service.retrieve(
                tenant_id=tenant_id,
                document_version_id=vector_version.document_version_id,
                query="proof of delivery",
            )
        ).accepted is True
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_natural_language_question_recalls_single_ready_active_chunk() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="smoke.txt",
    )
    _, chunk_id = await _add_generation(
        session_factory,
        version=version,
        text="Staging smoke contract. The evidence retention period is thirty days.",
        embedding=VECTOR_A,
    )
    query = "According to the document, what is the evidence retention period?"
    service = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=ControlledEmbeddingProvider({query: VECTOR_B}),
        top_k=5,
    )
    try:
        decision = await service.retrieve(
            tenant_id=tenant_id,
            document_version_id=version.document_version_id,
            query=query,
        )
        assert decision.accepted is True
        assert [candidate.chunk_id for candidate in decision.candidates] == [chunk_id]
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_retrieval_rejects_mismatched_version_generation_links() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    source_version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="source.txt",
    )
    target_version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="target.txt",
    )
    generation_id, source_chunk_id = await _add_generation(
        session_factory,
        version=source_version,
        text="authorized source marker",
        embedding=VECTOR_A,
    )
    mismatched_chunk_id = uuid4()
    mismatched_text = "cross version leak marker"
    async with session_factory.begin() as session:
        session.add(
            DocumentChunk(
                id=mismatched_chunk_id,
                tenant_id=tenant_id,
                document_version_id=target_version.document_version_id,
                generation_id=generation_id,
                chunk_index=1,
                heading=None,
                page_number=None,
                start_offset=0,
                end_offset=len(mismatched_text),
                normalized_text=mismatched_text,
                content_sha256=hashlib.sha256(mismatched_text.encode("utf-8")).hexdigest(),
                search_vector=func.to_tsvector("simple", mismatched_text),
                embedding=list(VECTOR_A),
            )
        )
    service = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=ControlledEmbeddingProvider({"leak": VECTOR_A}),
        top_k=5,
    )
    try:
        assert (
            await service._keyword_recall(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=target_version.document_version_id,
                query="leak",
            )
        ) == ()
        assert (
            await service._vector_recall(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=target_version.document_version_id,
                vector=VECTOR_A,
            )
        ) == ()
        source_candidates = await service._vector_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=source_version.document_version_id,
            vector=VECTOR_A,
        )
        assert [candidate.chunk_id for candidate in source_candidates] == [source_chunk_id]
        assert mismatched_chunk_id not in {candidate.chunk_id for candidate in source_candidates}
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_generation_switch_exposes_only_the_ready_active_generation() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    version = await _seed_version(
        session_factory,
        tenant_id=tenant_id,
        actor_id=actor_id,
        filename="switch.txt",
    )
    old_generation_id, old_chunk_id = await _add_generation(
        session_factory,
        version=version,
        text="legacy generation marker",
        embedding=VECTOR_A,
    )
    async with session_factory.begin() as session:
        await session.execute(
            update(DocumentIngestionGeneration)
            .where(DocumentIngestionGeneration.id == old_generation_id)
            .values(active=False)
        )
    new_generation_id, new_chunk_id = await _add_generation(
        session_factory,
        version=version,
        text="current generation marker",
        embedding=VECTOR_B,
        embedding_version=2,
    )
    service = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=ControlledEmbeddingProvider({"legacy": VECTOR_A, "current": VECTOR_B}),
        top_k=5,
    )
    try:
        assert (
            await service._keyword_recall(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=version.document_version_id,
                query="legacy",
            )
        ) == ()
        assert (
            await service._vector_recall(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=version.document_version_id,
                vector=VECTOR_A,
            )
        ) == ()
        current_keyword = await service._keyword_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=version.document_version_id,
            query="current",
        )
        current_vector = await service._vector_recall(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=version.document_version_id,
            vector=VECTOR_B,
        )
        for candidates in (current_keyword, current_vector):
            assert [candidate.chunk_id for candidate in candidates] == [new_chunk_id]
            assert {candidate.generation_id for candidate in candidates} == {new_generation_id}
            assert old_chunk_id not in {candidate.chunk_id for candidate in candidates}

        async with session_factory() as session:
            active_count = await session.scalar(
                select(func.count(DocumentIngestionGeneration.id)).where(
                    DocumentIngestionGeneration.document_version_id == version.document_version_id,
                    DocumentIngestionGeneration.active.is_(True),
                )
            )
        assert active_count == 1
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()
