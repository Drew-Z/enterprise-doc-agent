from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from tests.agent.test_agent_run_integration import _seed_agent_context
from tests.mcp.test_tool_service_integration import _context, _seed_running_tool_run

from enterprise_doc_core.agents.tools import (
    AgentToolService,
    SearchDocumentInput,
    ToolExecutionError,
)
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_usage import ProductUsageService
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment, EmbeddingSettings
from enterprise_doc_core.documents.embedding_provider import OpenAICompatibleEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["missing", "expired_period", "released_during_retry"])
async def test_formal_tool_query_requires_original_live_reservation(billing_database, mode):
    sessions, _ = billing_database
    seed = await _seed_agent_context(sessions)
    calls = []
    usage = ProductUsageService(session_factory=sessions, app_env=AppEnvironment.PRODUCTION)
    try:
        run, execution_id, _, _ = await _seed_running_tool_run(
            sessions, seed, idempotency_key="query-metering"
        )
        now = datetime.now(UTC)
        if mode != "missing":
            # Reserve in an old period using the business clock; the subsequent
            # real HTTP dispatch is allowed to outlive that period during review.
            admitted = now - timedelta(days=2) if mode == "expired_period" else now
            await EntitlementAdministrationService(
                session_factory=sessions, clock=lambda: admitted
            ).configure(
                tenant_id=seed.tenant_id,
                operator=PlatformEntitlementOperator("test", "query admission"),
                configuration=EntitlementConfiguration(
                    entitlement_id=uuid4(),
                    expected_version=0,
                    plan_code="invited",
                    period_start=admitted - timedelta(minutes=1),
                    period_end=admitted + timedelta(days=1),
                    provider_request_limit=0,
                    agent_task_limit=1,
                ),
            )
            await ProductUsageService(
                session_factory=sessions,
                app_env=AppEnvironment.PRODUCTION,
                clock=lambda: admitted,
            ).reserve(
                tenant_id=seed.tenant_id, operation_id=run.run_id, metric=ProductMetric.AGENT_TASK
            )

        async def provider(request):
            calls.append(request)
            if mode == "released_during_retry":
                await usage.release(
                    tenant_id=seed.tenant_id,
                    operation_id=run.run_id,
                    metric=ProductMetric.AGENT_TASK,
                    source="test.cancelled",
                )
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1024}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            retrieval = HybridRetrievalService(
                session_factory=sessions,
                embedding_provider=OpenAICompatibleEmbeddingProvider(
                    settings=EmbeddingSettings(
                        provider="openai_compatible",
                        base_url="https://provider.test/v1",
                        api_key="test-only",
                        model_name="embed",
                        retry_base_seconds=0.001,
                    ),
                    client=client,
                    require_metering=True,
                ),
                app_env=AppEnvironment.PRODUCTION,
            )
            service = AgentToolService(
                session_factory=sessions,
                retrieval_service=retrieval,
                app_env=AppEnvironment.PRODUCTION,
            )
            context = _context(
                tenant_id=seed.tenant_id,
                actor_id=seed.actor_id,
                run_id=run.run_id,
                execution_id=execution_id,
                document_version_id=seed.document_version_id,
            )
            request = SearchDocumentInput(idempotency_key="query", query="payment terms")
            if mode == "expired_period":
                assert (await service.search_document(context, request)).accepted
                # A forged tenant cannot dispatch against another tenant's version.
                with pytest.raises(UsageError, match="provider_query_forbidden"):
                    await retrieval.retrieve(
                        tenant_id=billing_database[1][0],
                        actor_id=seed.actor_id,
                        document_version_id=seed.document_version_id,
                        query="payment terms",
                    )
            else:
                with pytest.raises(ToolExecutionError):
                    await service.search_document(context, request)
        assert len(calls) == (0 if mode == "missing" else 1)
        async with sessions() as session:
            receipts = (
                await session.scalars(
                    select(ProviderDispatch).where(ProviderDispatch.tenant_id == seed.tenant_id)
                )
            ).all()
            assert len(receipts) == len(calls)
            assert all(r.kind == "query" and r.estimated_cost is None for r in receipts)
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == seed.tenant_id))
            await session.execute(delete(User).where(User.id == seed.actor_id))
