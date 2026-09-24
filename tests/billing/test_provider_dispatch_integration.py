import asyncio
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from enterprise_doc_core.agents import AgentRunTaskType, BehaviorVersions, GroundedModelRequest
from enterprise_doc_core.agents.gateway import OpenAICompatibleChatGateway
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.provider_calls import ProviderCallService, recorded_post
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.billing.service import EntitlementUsageService
from enterprise_doc_core.config import EmbeddingSettings, ModelSettings, ProviderUsageSettings
from enterprise_doc_core.documents.embedding_provider import OpenAICompatibleEmbeddingProvider

pytestmark = pytest.mark.integration


async def test_dispatch_fk_does_not_deadlock_with_quota_admission(billing_database):
    sessions, (tenant_id, _) = billing_database
    service = ProviderCallService(session_factory=sessions)
    business_locked, tenant_locked = asyncio.Event(), asyncio.Event()
    key = f"metering-test:{uuid4()}"

    async def business_lock(session):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    async def guard(session):
        await business_lock(session)
        business_locked.set()
        await asyncio.wait_for(tenant_locked.wait(), 5)

    async def admission():
        await asyncio.wait_for(business_locked.wait(), 5)
        async with sessions.begin() as session:
            await lock_usage_tenant(session, tenant_id)
            tenant_locked.set()
            await business_lock(session)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:

        async def dispatch():
            with service.scope(
                tenant_id=tenant_id, operation_id=uuid4(), kind="agent", guard=guard
            ):
                await recorded_post(
                    client,
                    "https://provider.test/chat/completions",
                    provider="openai_compatible",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                )

        tasks = [asyncio.create_task(dispatch()), asyncio.create_task(admission())]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_empty_dispatch_migration_roundtrip(billing_database):
    sessions, _ = billing_database
    migration = import_module(
        "enterprise_doc_core.db.migrations.versions.20260924_0030_provider_dispatches"
    )

    def roundtrip(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    async with sessions.begin() as session:
        await (await session.connection()).run_sync(roundtrip)


async def test_adapters_record_each_repair_split_and_retry(billing_database):
    sessions, (tenant_id, _) = billing_database
    service = ProviderCallService(session_factory=sessions)
    chat_calls, embed_calls = [], []

    def provider(request):
        body = json.loads(request.content)
        if request.url.path.endswith("chat/completions"):
            chat_calls.append(body)
            content = (
                "broken"
                if len(chat_calls) == 1
                else json.dumps(
                    {
                        "outcome": "refusal",
                        "task_type": "question_answer",
                        "refusal_reason": "insufficient_evidence",
                        "answer_text": None,
                        "structured_fields": None,
                        "citations": [],
                        "risk_hint": None,
                    }
                )
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        embed_calls.append(body)
        if len(body["input"]) > 1:
            return httpx.Response(413)
        if len(embed_calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1024}]})

    async def guard(session):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        gateway = OpenAICompatibleChatGateway(
            settings=ModelSettings(
                provider="openai_compatible",
                base_url="https://provider.test/v1",
                api_key="test-only",
                model_name="chat",
            ),
            client=client,
            require_metering=True,
        )
        embeddings = OpenAICompatibleEmbeddingProvider(
            settings=EmbeddingSettings(
                provider="openai_compatible",
                base_url="https://provider.test/v1",
                api_key="test-only",
                model_name="embed",
                retry_base_seconds=0.001,
            ),
            client=client,
            require_metering=True,
        )
        with service.scope(tenant_id=tenant_id, operation_id=uuid4(), kind="agent", guard=guard):
            output = await gateway.generate(
                GroundedModelRequest(
                    task_type=AgentRunTaskType.QUESTION_ANSWER,
                    user_input="question",
                    evidence=[],
                    behavior_versions=BehaviorVersions(
                        graph_version="m4.v1", prompt_version="m4.v2", tool_schema_version="m4.v1"
                    ),
                )
            )
        assert output.repaired
        with service.scope(tenant_id=tenant_id, operation_id=uuid4(), kind="document", guard=guard):
            assert len(await embeddings.embed(("a", "b"))) == 2
    assert len(chat_calls) == 2 and len(embed_calls) == 4
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).all()
        assert len(rows) == 6
        assert sum(r.state == "http_error" for r in rows) == 2
        assert all(r.estimated_cost is None and r.currency is None for r in rows)


async def test_dispatch_receipts_survive_timeout_and_cannot_exceed_daily_budget(billing_database):
    sessions, (tenant_id, _) = billing_database
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("test", "usage visibility"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
        ),
    )
    service = ProviderCallService(
        session_factory=sessions, settings=ProviderUsageSettings(daily_call_limit=2)
    )
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("sensitive upstream details", request=request)
        return httpx.Response(200, json={"usage": {"total_tokens": 7}})

    async def guard(session):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with service.scope(tenant_id=tenant_id, operation_id=uuid4(), kind="agent", guard=guard):
            with pytest.raises(httpx.ReadTimeout):
                await recorded_post(
                    client,
                    "https://primary.test/chat/completions",
                    provider="openai_compatible",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                )
            await recorded_post(
                client,
                "https://fallback.test/chat/completions",
                provider="openai_compatible",
                model="test",
                json_body={},
                headers={},
                request_timeout=httpx.Timeout(1),
                require_metering=True,
            )
            with pytest.raises(UsageError, match="provider_daily_budget_exhausted"):
                await recorded_post(
                    client,
                    "https://fallback.test/chat/completions",
                    provider="openai_compatible",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                )
    assert len(calls) == 2
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(ProviderDispatch)
                .where(ProviderDispatch.tenant_id == tenant_id)
                .order_by(ProviderDispatch.started_at)
            )
        ).all()
        assert [r.state for r in rows] == ["timeout", "responded"]
        assert [r.total_tokens for r in rows] == [None, 7]
        assert len({r.channel_hash for r in rows}) == 2
    summary = await EntitlementUsageService(session_factory=sessions).summary(tenant_id=tenant_id)
    assert summary.model_calls.calls == 2
    assert summary.model_calls.unresolved_calls == 1
    assert summary.model_calls.unknown_cost_calls == 2
    assert summary.model_calls.usage_known_calls == 1
    assert summary.model_calls.known_total_tokens == 7
    migration = import_module(
        "enterprise_doc_core.db.migrations.versions.20260924_0030_provider_dispatches"
    )

    def downgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    with pytest.raises(RuntimeError, match="provider_dispatch_history_present"):
        async with sessions.begin() as session:
            await (await session.connection()).run_sync(downgrade)


async def test_dispatch_guard_and_required_scope_block_before_network(billing_database):
    sessions, (tenant_id, _) = billing_database
    service = ProviderCallService(session_factory=sessions)

    async def stale(session):
        raise UsageError("test_execution_stale")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("provider must not be dispatched"))
    ) as client:
        with pytest.raises(UsageError, match="provider_metering_context_missing"):
            await recorded_post(
                client,
                "https://provider.test/embeddings",
                provider="openai_compatible",
                model="test",
                json_body={},
                headers={},
                request_timeout=httpx.Timeout(1),
                require_metering=True,
            )
        with service.scope(tenant_id=tenant_id, operation_id=uuid4(), kind="document", guard=stale):
            with pytest.raises(UsageError, match="test_execution_stale"):
                await recorded_post(
                    client,
                    "https://provider.test/embeddings",
                    provider="openai_compatible",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                )
    async with sessions() as session:
        assert not (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).all()


async def test_concurrent_last_dispatch_and_cancellation_keep_cost_unknown(billing_database):
    sessions, (tenant_id, _) = billing_database
    service = ProviderCallService(
        session_factory=sessions, settings=ProviderUsageSettings(daily_call_limit=1)
    )
    started = asyncio.Event()

    async def provider(request):
        started.set()
        await asyncio.Event().wait()

    async def guard(session):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:

        async def run():
            with service.scope(
                tenant_id=tenant_id, operation_id=uuid4(), kind="query", guard=guard
            ):
                return await recorded_post(
                    client,
                    "https://provider.test/embeddings",
                    provider="openai_compatible",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                )

        first = asyncio.create_task(run())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            with pytest.raises(UsageError, match="provider_daily_budget_exhausted"):
                await run()
        finally:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
    async with sessions() as session:
        row = await session.scalar(
            select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
        )
        assert row.state == "cancelled" and row.total_tokens is None
