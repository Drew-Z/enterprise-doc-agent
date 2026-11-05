import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import DBAPIError
from tests.jobs.test_m3_retrieval_integration import (
    VECTOR_A,
    _add_generation,
    _seed_identity,
    _seed_version,
)

from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment, DatabaseSettings, EmbeddingSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.embedding_provider import OpenAICompatibleEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User

pytestmark = pytest.mark.integration


@pytest.fixture
async def metered_retrieval():
    engine = create_database_engine(
        DatabaseSettings(
            pool_size=1,
            max_overflow=0,
            prepare_threshold=1,
        )
    )
    sessions = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(sessions)
    calls = []
    try:
        version = await _seed_version(
            sessions, tenant_id=tenant_id, actor_id=actor_id, filename="read-cache.txt"
        )
        _, chunk_id = await _add_generation(
            sessions, version=version, text="retention", embedding=VECTOR_A
        )
        now = datetime.now(UTC)
        async with sessions.begin() as session:
            session.add(
                TenantEntitlement(
                    tenant_id=tenant_id,
                    plan_code="invited",
                    version=1,
                    period_start=now - timedelta(minutes=1),
                    period_end=now + timedelta(hours=1),
                    provider_request_limit=10,
                )
            )

        def provider(request):
            calls.append(request)
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": VECTOR_A}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            service = HybridRetrievalService(
                session_factory=sessions,
                embedding_provider=OpenAICompatibleEmbeddingProvider(
                    settings=EmbeddingSettings(
                        provider="openai_compatible",
                        base_url="https://provider.test/v1",
                        api_key="test-only",
                        model_name="controlled",
                    ),
                    client=client,
                    require_metering=True,
                ),
                app_env=AppEnvironment.PRODUCTION,
            )
            yield service, sessions, engine, version, chunk_id, calls
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


async def test_repeated_metered_retrieval_retains_prepared_recall_queries(metered_retrieval):
    service, sessions, _, version, chunk_id, calls = metered_retrieval
    for _ in range(4):
        decision = await service.retrieve(
            tenant_id=version.tenant_id,
            actor_id=version.actor_id,
            document_version_id=version.document_version_id,
            query="retention",
        )
        assert decision.accepted
        assert [candidate.chunk_id for candidate in decision.candidates] == [chunk_id]
    async with sessions.begin() as session:
        queries = (
            await session.scalars(text("SELECT statement FROM pg_prepared_statements"))
        ).all()
        # Both read phases must leave their prepared statements in the actual
        # server catalog despite intervening committed provider-metering writes.
        assert any("primary_keyword_recall" in query for query in queries)
        assert any("document_chunks.embedding <=>" in query for query in queries)
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
        dispatches = (
            await session.scalars(
                select(ProviderDispatch).where(
                    ProviderDispatch.tenant_id == version.tenant_id,
                )
            )
        ).all()
    assert len(calls) == 4
    assert len(dispatches) == 4 and all(d.state == "responded" for d in dispatches)


@pytest.mark.parametrize("phase", ["keyword", "vector"])
@pytest.mark.parametrize("fault", ["write", "cancel"])
async def test_recall_failure_enforces_readonly_then_restores_metering(
    metered_retrieval, phase, fault
):
    service, sessions, engine, version, chunk_id, calls = metered_retrieval
    async with sessions.begin() as session:
        original_name = await session.scalar(
            select(Tenant.name).where(Tenant.id == version.tenant_id)
        )
    injected = []

    def inject(connection, _cursor, statement, _parameters, _context, _many):
        matches = (
            "primary_keyword_recall" in statement
            if phase == "keyword"
            else "document_chunks.embedding <=>" in statement
        )
        if not matches:
            return
        injected.append(phase)
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        if fault == "cancel":
            raise asyncio.CancelledError()
        connection.exec_driver_sql(
            "UPDATE tenants SET name = 'must-not-be-committed' WHERE id = %(id)s",
            {"id": version.tenant_id},
        )

    event.listen(engine.sync_engine, "before_cursor_execute", inject)
    try:
        with pytest.raises(DBAPIError if fault == "write" else asyncio.CancelledError) as caught:
            await service.retrieve(
                tenant_id=version.tenant_id,
                actor_id=version.actor_id,
                document_version_id=version.document_version_id,
                query="retention",
            )
        if fault == "write":
            assert caught.value.orig.sqlstate == "25006"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", inject)
    assert injected == [phase]
    assert len(calls) == (0 if phase == "keyword" else 1)
    async with sessions.begin() as session:
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
        assert (
            await session.scalar(select(Tenant.name).where(Tenant.id == version.tenant_id))
            == original_name
        )
        assert (
            await session.execute(
                text("UPDATE tenants SET name = name WHERE id = :id"), {"id": version.tenant_id}
            )
        ).rowcount == 1
    decision = await service.retrieve(
        tenant_id=version.tenant_id,
        actor_id=version.actor_id,
        document_version_id=version.document_version_id,
        query="retention",
    )
    assert decision.accepted and [c.chunk_id for c in decision.candidates] == [chunk_id]
    async with sessions.begin() as session:
        dispatches = (
            await session.scalars(
                select(ProviderDispatch).where(
                    ProviderDispatch.tenant_id == version.tenant_id,
                )
            )
        ).all()
    assert len(dispatches) == len(calls) == (1 if phase == "keyword" else 2)
    assert all(row.state == "responded" for row in dispatches)
