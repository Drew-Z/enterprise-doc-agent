from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, event, select
from tests.jobs.test_m3_retrieval_integration import (
    VECTOR_A,
    _add_generation,
    _seed_identity,
    _seed_version,
)

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment, EmbeddingSettings
from enterprise_doc_core.documents import retrieval_service as retrieval_module
from enterprise_doc_core.documents.embedding_provider import OpenAICompatibleEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User

pytestmark = pytest.mark.integration


async def test_formal_query_uses_eight_sql_roundtrips_with_committed_metering(billing_database):
    sessions, _ = billing_database
    tenant_id, actor_id = await _seed_identity(sessions)
    statements, calls = [], []
    operation_id = uuid4()
    engine = sessions.kw["bind"]

    def record_sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    try:
        version = await _seed_version(
            sessions, tenant_id=tenant_id, actor_id=actor_id, filename="guard.txt"
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
            retrieval = HybridRetrievalService(
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
            event.listen(engine.sync_engine, "before_cursor_execute", record_sql)
            try:
                decision = await retrieval.retrieve(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    document_version_id=version.document_version_id,
                    query="retention",
                    provider_operation_id=operation_id,
                )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", record_sql)
        assert decision.accepted
        assert [c.chunk_id for c in decision.candidates] == [chunk_id]
        assert len(calls) == 1
        async with sessions() as session:
            dispatch = (
                await session.scalars(
                    select(ProviderDispatch).where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.operation_id == operation_id,
                    )
                )
            ).one()
        assert dispatch.state == "responded" and dispatch.kind == "query"
        assert len(statements) <= 8
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("missing", "usage_entitlement_inactive"),
        ("expired", "usage_entitlement_inactive"),
        ("future", "usage_entitlement_inactive"),
        ("unlimited", "usage_entitlement_inactive"),
        ("end_boundary", "usage_entitlement_inactive"),
        ("start_boundary", None),
        ("zero_finite_limit", None),
        ("local_missing", None),
        ("invisible_missing", "provider_query_forbidden"),
        ("cross_tenant_missing", "provider_query_forbidden"),
        ("cross_tenant_active", "provider_query_forbidden"),
    ],
)
async def test_query_guard_keeps_period_boundaries_and_forbidden_precedence(
    billing_database, monkeypatch, mode, expected_error
):
    sessions, (other_tenant, _) = billing_database
    tenant_id, actor_id = await _seed_identity(sessions)
    now = datetime.now(UTC).replace(microsecond=0)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(retrieval_module, "datetime", FrozenDatetime)
    calls = []
    operation_id = uuid4()
    try:
        version = await _seed_version(
            sessions, tenant_id=tenant_id, actor_id=actor_id, filename="guard-boundaries.txt"
        )
        await _add_generation(sessions, version=version, text="retention", embedding=VECTOR_A)
        request_tenant = other_tenant if mode.startswith("cross_tenant") else tenant_id
        request_actor = uuid4() if mode == "invisible_missing" else actor_id
        if mode not in {"missing", "local_missing", "invisible_missing", "cross_tenant_missing"}:
            start, end = now - timedelta(hours=1), now + timedelta(hours=1)
            if mode == "expired":
                start, end = now - timedelta(hours=2), now - timedelta(hours=1)
            elif mode == "future":
                start, end = now + timedelta(hours=1), now + timedelta(hours=2)
            elif mode == "end_boundary":
                end = now
            elif mode == "start_boundary":
                start = now
            async with sessions.begin() as session:
                session.add(
                    TenantEntitlement(
                        tenant_id=request_tenant,
                        plan_code="invited",
                        version=1,
                        period_start=start,
                        period_end=end,
                        provider_request_limit=None
                        if mode == "unlimited"
                        else 0
                        if mode == "zero_finite_limit"
                        else 10,
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
                app_env=AppEnvironment.LOCAL
                if mode == "local_missing"
                else AppEnvironment.PRODUCTION,
            )

            async def retrieve():
                return await service.retrieve(
                    tenant_id=request_tenant,
                    actor_id=request_actor,
                    document_version_id=version.document_version_id,
                    query="retention",
                    provider_operation_id=operation_id,
                )

            if expected_error:
                with pytest.raises(UsageError, match=f"^{expected_error}$"):
                    await retrieve()
            else:
                assert (await retrieve()).accepted
        async with sessions() as session:
            dispatches = (
                await session.scalars(
                    select(ProviderDispatch).where(
                        ProviderDispatch.operation_id == operation_id,
                    )
                )
            ).all()
        assert len(calls) == len(dispatches) == (0 if expected_error else 1)
        assert all(row.state == "responded" for row in dispatches)
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
