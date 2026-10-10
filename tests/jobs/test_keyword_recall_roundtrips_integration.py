from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, update
from tests.jobs.test_m3_retrieval_integration import (
    VECTOR_A,
    VECTOR_B,
    ControlledEmbeddingProvider,
    _add_generation,
    _seed_identity,
    _seed_version,
)

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.retrieval import RefusalReason
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Membership, Tenant, User

pytestmark = pytest.mark.integration


async def test_natural_question_uses_two_recall_roundtrips_with_original_evidence() -> None:
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(sessions)
    statements: list[str] = []

    def record_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    try:
        version = await _seed_version(
            sessions, tenant_id=tenant_id, actor_id=actor_id, filename="retention.txt"
        )
        generation_id, chunk_id = await _add_generation(
            sessions,
            version=version,
            text="The evidence retention period is thirty days.",
            embedding=VECTOR_A,
        )
        query = "According to the document, what is the evidence retention period?"
        service = HybridRetrievalService(
            session_factory=sessions,
            embedding_provider=ControlledEmbeddingProvider({query: VECTOR_B}),
            top_k=5,
        )
        event.listen(engine.sync_engine, "before_cursor_execute", record_sql)
        try:
            decision = await service.retrieve(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=version.document_version_id,
                query=query,
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record_sql)
        assert decision.accepted
        assert len(decision.candidates) == 1
        candidate = decision.candidates[0]
        assert candidate.chunk_id == chunk_id
        assert candidate.generation_id == generation_id
        assert candidate.source_filename == "retention.txt"
        assert candidate.text == "The evidence retention period is thirty days."
        assert candidate.score == pytest.approx(1 / 61)
        # Counts every SQL statement, including WITH. The external embedding
        # boundary is controlled here; production metering has separate tests.
        assert len(statements) == 2
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


@pytest.fixture
async def recall_database():
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(sessions)
    try:
        version = await _seed_version(
            sessions, tenant_id=tenant_id, actor_id=actor_id, filename="evidence.txt"
        )
        yield sessions, engine, version
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


async def _extra_chunk(sessions, version, generation_id, index, text):
    chunk_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            DocumentChunk(
                id=chunk_id,
                tenant_id=version.tenant_id,
                document_version_id=version.document_version_id,
                generation_id=generation_id,
                chunk_index=index,
                normalized_text=text,
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                search_vector=func.to_tsvector("simple", text),
                embedding=list(VECTOR_A),
                page_number=index + 1,
                heading=f"Section {index}",
                start_offset=100 * index,
                end_offset=100 * index + len(text),
            )
        )
        await session.execute(
            update(DocumentIngestionGeneration)
            .where(DocumentIngestionGeneration.id == generation_id)
            .values(chunk_count=index + 1, embedded_count=index + 1)
        )
    return chunk_id


async def test_primary_keyword_result_does_not_fill_with_fallback(recall_database):
    sessions, _, version = recall_database
    generation_id, primary_id = await _add_generation(
        sessions, version=version, text="evidence retention period", embedding=VECTOR_A
    )
    await _extra_chunk(sessions, version, generation_id, 1, "evidence evidence evidence")
    query = "evidence retention"
    decision = await HybridRetrievalService(
        session_factory=sessions,
        embedding_provider=ControlledEmbeddingProvider({query: VECTOR_B}),
        top_k=5,
    ).retrieve(
        tenant_id=version.tenant_id,
        actor_id=version.actor_id,
        document_version_id=version.document_version_id,
        query=query,
    )
    assert decision.accepted
    assert [item.chunk_id for item in decision.candidates] == [primary_id]


@pytest.mark.parametrize("query", ["retention", "what is the retention requirement?"])
async def test_primary_and_fallback_keep_rank_ties_top_k_and_metadata(recall_database, query):
    sessions, _, version = recall_database
    generation_id, first_id = await _add_generation(
        sessions, version=version, text="retention", embedding=VECTOR_A
    )
    second_id = await _extra_chunk(sessions, version, generation_id, 1, "retention")
    await _extra_chunk(sessions, version, generation_id, 2, "retention")
    decision = await HybridRetrievalService(
        session_factory=sessions,
        embedding_provider=ControlledEmbeddingProvider({query: VECTOR_B}),
        top_k=2,
    ).retrieve(
        tenant_id=version.tenant_id,
        actor_id=version.actor_id,
        document_version_id=version.document_version_id,
        query=query,
    )
    assert decision.accepted
    assert [item.chunk_id for item in decision.candidates] == [first_id, second_id]
    assert [item.score for item in decision.candidates] == pytest.approx([1 / 61, 1 / 62])
    second = decision.candidates[1]
    assert (second.page_number, second.heading, second.start_offset, second.end_offset) == (
        2,
        "Section 1",
        100,
        109,
    )


@pytest.mark.parametrize("query", ["", "what is", "unmatched words"])
async def test_empty_stopword_and_unmatched_queries_keep_refusal(recall_database, query):
    sessions, _, version = recall_database
    await _add_generation(sessions, version=version, text="retention", embedding=VECTOR_A)
    decision = await HybridRetrievalService(
        session_factory=sessions,
        embedding_provider=ControlledEmbeddingProvider({query: VECTOR_B}),
    ).retrieve(
        tenant_id=version.tenant_id,
        actor_id=version.actor_id,
        document_version_id=version.document_version_id,
        query=query,
    )
    assert not decision.accepted
    assert decision.candidates == ()
    assert decision.refusal_reason == RefusalReason.EMPTY_EVIDENCE


@pytest.mark.parametrize(
    "exclusion",
    [
        "inactive_generation",
        "failed_generation",
        "not_ready",
        "wrong_model",
        "wrong_dimension",
        "wrong_generation_version",
        "wrong_generation_tenant",
        "wrong_chunk_tenant",
        "wrong_version_tenant",
        "wrong_document_tenant",
        "inactive_membership",
        "inactive_user",
        "inactive_tenant",
        "ungranted_actor",
        "wrong_request_tenant",
    ],
)
@pytest.mark.parametrize("query", ["retention", "what is the retention requirement?"])
async def test_both_keyword_branches_preserve_scope_filters(recall_database, exclusion, query):
    sessions, _, version = recall_database
    generation_id, chunk_id = await _add_generation(
        sessions, version=version, text="retention", embedding=VECTOR_A
    )
    extra_tenant = None
    extra_user = None
    tenant_id = version.tenant_id
    actor_id = version.actor_id
    try:
        async with sessions.begin() as session:
            if exclusion in {
                "wrong_generation_tenant",
                "wrong_chunk_tenant",
                "wrong_version_tenant",
                "wrong_document_tenant",
                "wrong_request_tenant",
            }:
                extra_tenant = uuid4()
                session.add(
                    Tenant(
                        id=extra_tenant,
                        name="other",
                        slug=f"other-{extra_tenant}",
                        quota_bytes=1024 * 1024,
                    )
                )
                await session.flush()
            generation_changes = {
                "inactive_generation": {"active": False},
                "failed_generation": {"status": "failed"},
                "not_ready": {"stage": "embed"},
                "wrong_model": {"embedding_model": "other-model"},
                "wrong_dimension": {"embedding_dimension": 512},
                "wrong_generation_tenant": {"tenant_id": extra_tenant},
            }
            if exclusion in generation_changes:
                await session.execute(
                    update(DocumentIngestionGeneration)
                    .where(DocumentIngestionGeneration.id == generation_id)
                    .values(**generation_changes[exclusion])
                )
            elif exclusion == "wrong_chunk_tenant":
                await session.execute(
                    update(DocumentChunk)
                    .where(DocumentChunk.id == chunk_id)
                    .values(tenant_id=extra_tenant)
                )
            elif exclusion == "wrong_version_tenant":
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.id == version.document_version_id)
                    .values(tenant_id=extra_tenant)
                )
            elif exclusion == "wrong_document_tenant":
                await session.execute(
                    update(Document)
                    .where(Document.tenant_id == version.tenant_id)
                    .values(tenant_id=extra_tenant)
                )
            elif exclusion == "inactive_membership":
                await session.execute(
                    update(Membership)
                    .where(Membership.tenant_id == version.tenant_id)
                    .values(is_active=False)
                )
            elif exclusion == "inactive_user":
                await session.execute(
                    update(User).where(User.id == actor_id).values(is_active=False)
                )
            elif exclusion == "inactive_tenant":
                await session.execute(
                    update(Tenant).where(Tenant.id == tenant_id).values(is_active=False)
                )
            elif exclusion == "ungranted_actor":
                extra_user = uuid4()
                session.add(User(id=extra_user, email=f"viewer-{extra_user}@example.test"))
                await session.flush()
                session.add(Membership(tenant_id=tenant_id, user_id=extra_user, role="member"))
                await session.execute(
                    update(Document)
                    .where(Document.tenant_id == tenant_id)
                    .values(access_mode="restricted")
                )
                actor_id = extra_user
            elif exclusion == "wrong_request_tenant":
                tenant_id = extra_tenant
        if exclusion == "wrong_generation_version":
            other_version = await _seed_version(
                sessions, tenant_id=tenant_id, actor_id=actor_id, filename="other.txt"
            )
            async with sessions.begin() as session:
                await session.execute(
                    update(DocumentIngestionGeneration)
                    .where(DocumentIngestionGeneration.id == generation_id)
                    .values(document_version_id=other_version.document_version_id)
                )
        decision = await HybridRetrievalService(
            session_factory=sessions,
            embedding_provider=ControlledEmbeddingProvider({query: VECTOR_A}),
            embedding_model="controlled",
        ).retrieve(
            tenant_id=tenant_id,
            actor_id=actor_id,
            document_version_id=version.document_version_id,
            query=query,
        )
        assert not decision.accepted
        assert decision.candidates == ()
    finally:
        async with sessions.begin() as session:
            if extra_tenant is not None:
                # First remove the original tenant and its upload session so a
                # deliberately mismatched version cannot block cascade cleanup.
                await session.execute(delete(Tenant).where(Tenant.id == version.tenant_id))
                await session.execute(delete(Tenant).where(Tenant.id == extra_tenant))
            if extra_user is not None:
                await session.execute(delete(User).where(User.id == extra_user))
