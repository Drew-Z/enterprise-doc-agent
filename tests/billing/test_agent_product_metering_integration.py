import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, select
from tests.agent.test_agent_run_integration import _request, _seed_agent_context
from tests.agent.test_graph_worker_integration import DatabaseBackedFakeMcpClient

from enterprise_doc_core.agents import (
    AgentGraphError,
    AgentRun,
    AgentRunService,
    AgentRunTaskType,
    BehaviorVersions,
    DeterministicGroundedGateway,
    GroundedModelRequest,
)
from enterprise_doc_core.agents.gateway import OpenAICompatibleChatGateway
from enterprise_doc_core.agents.service import AgentRunError
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.product_models import (
    ProductQuota,
    ProductUsageEvent,
    ProductUsageReservation,
)
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AgentSettings, AppEnvironment, McpSettings, ModelSettings
from enterprise_doc_core.documents.models import DEFAULT_EMBEDDING_DIMENSION, DocumentChunk
from enterprise_doc_core.identity.models import Tenant, User
from enterprise_doc_core.jobs import Job, JobRuntimeService
from enterprise_doc_worker.agent_backend import DurableAgentGraphBackend
from enterprise_doc_worker.agent_handler import (
    AgentExecutionPayload,
    SqlAlchemyAgentExecutionLoader,
)
from enterprise_doc_worker.agents import build_durable_agent_handler

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("loss", ["cancel", "fence"])
async def test_model_repair_rechecks_job_and_reservation_before_dispatch(metered_agent, loss):
    sessions, context = metered_agent
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(),
        model_settings=ModelSettings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    created = await service.create(
        principal=context.principal, idempotency_key="repair-fence", request=_request(context)
    )
    runtime = JobRuntimeService(session_factory=sessions)
    claim = await runtime.claim(job_id=created.job_id, worker_id="repair-worker")
    execution = await SqlAlchemyAgentExecutionLoader(sessions).load(
        claim, AgentExecutionPayload.model_validate(claim.payload)
    )
    mcp = McpSettings()
    calls = []

    async def provider(request):
        calls.append(request)
        if loss == "cancel":
            await service.cancel(
                run_id=created.run_id, tenant_id=context.tenant_id, actor_id=context.actor_id
            )
        else:
            async with sessions.begin() as session:
                job = await session.get(Job, created.job_id)
                job.fencing_token += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "malformed"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        gateway = OpenAICompatibleChatGateway(
            settings=ModelSettings(
                provider="openai_compatible",
                base_url="https://provider.test/v1",
                api_key="test-only",
                model_name="test",
            ),
            client=client,
            require_metering=True,
        )
        backend = DurableAgentGraphBackend(
            session_factory=sessions,
            context=execution,
            gateway=gateway,
            mcp_client=DatabaseBackedFakeMcpClient(session_factory=sessions, settings=mcp),
            mcp_settings=mcp,
            app_env=AppEnvironment.PRODUCTION,
        )
        await backend.prepare_segment()
        with backend.provider_scope(), pytest.raises(AgentGraphError):
            await gateway.generate(
                GroundedModelRequest(
                    task_type=AgentRunTaskType.QUESTION_ANSWER,
                    user_input="question",
                    evidence=[],
                    behavior_versions=BehaviorVersions(
                        graph_version="m4.v1", prompt_version="m4.v2", tool_schema_version="m4.v1"
                    ),
                )
            )
    assert len(calls) == 1
    async with sessions() as session:
        receipts = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == context.tenant_id)
            )
        ).all()
        assert len(receipts) == 1 and receipts[0].state == "responded"
        assert receipts[0].estimated_cost is None
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert quota.units_used == 0


@pytest.fixture
async def metered_agent(billing_database):
    sessions, _ = billing_database
    context = await _seed_agent_context(sessions)
    try:
        now = datetime.now(UTC)
        await EntitlementAdministrationService(session_factory=sessions).configure(
            tenant_id=context.tenant_id,
            operator=PlatformEntitlementOperator("agent-test", "one successful task"),
            configuration=EntitlementConfiguration(
                entitlement_id=uuid4(),
                expected_version=0,
                plan_code="invited",
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(days=1),
                provider_request_limit=5,
                agent_task_limit=1,
                document_bytes_limit=1024,
            ),
        )
        yield sessions, context
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == context.tenant_id))
            await session.execute(delete(User).where(User.id == context.actor_id))


async def test_agent_create_replay_and_cancel_share_one_atomic_reservation(metered_agent):
    sessions, context = metered_agent
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(),
        model_settings=ModelSettings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    created = await service.create(
        principal=context.principal, idempotency_key="task-one", request=_request(context)
    )
    replay = await service.create(
        principal=context.principal, idempotency_key="task-one", request=_request(context)
    )
    assert replay.replayed and replay.run_id == created.run_id
    with pytest.raises(AgentRunError) as rejected:
        await service.create(
            principal=context.principal, idempotency_key="task-two", request=_request(context)
        )
    assert rejected.value.code == "agent_usage_limit"
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.tenant_id == context.tenant_id)
            )
            == 1
        )
        reservation = await session.scalar(
            select(ProductUsageReservation).where(
                ProductUsageReservation.tenant_id == context.tenant_id
            )
        )
        assert reservation.operation_id == created.run_id and reservation.state == "reserved"
    for _ in range(2):
        cancelled = await service.cancel(
            run_id=created.run_id, tenant_id=context.tenant_id, actor_id=context.actor_id
        )
        assert cancelled.status == "cancelled"
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (0, 0)
    next_run = await service.create(
        principal=context.principal, idempotency_key="task-two", request=_request(context)
    )
    assert next_run.run_id != created.run_id


async def test_formal_worker_success_consumes_one_task_with_terminal_event(metered_agent):
    sessions, context = metered_agent
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(),
        model_settings=ModelSettings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    source = "Payment is due within 30 days after acceptance."
    async with sessions.begin() as session:
        session.add(
            DocumentChunk(
                tenant_id=context.tenant_id,
                document_version_id=context.document_version_id,
                generation_id=context.generation_id,
                chunk_index=0,
                heading="Payment",
                page_number=1,
                start_offset=0,
                end_offset=len(source),
                normalized_text=source,
                content_sha256=hashlib.sha256(source.encode()).hexdigest(),
                search_vector="'payment':1",
                embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
            )
        )
    created = await service.create(
        principal=context.principal, idempotency_key="complete-task", request=_request(context)
    )
    runtime = JobRuntimeService(session_factory=sessions)
    claim = await runtime.claim(job_id=created.job_id, worker_id="metered-worker")
    assert claim is not None
    settings = McpSettings()
    handler = build_durable_agent_handler(
        session_factory=sessions,
        model_settings=ModelSettings(),
        mcp_settings=settings,
        mcp_client=DatabaseBackedFakeMcpClient(session_factory=sessions, settings=settings),
        checkpointer=InMemorySaver(),
        app_env=AppEnvironment.PRODUCTION,
    )
    await handler(claim)
    assert await runtime.succeed(claim) == "succeeded"
    status = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
    assert status.status == "succeeded"
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (1, 0)
        events = (
            await session.scalars(
                select(ProductUsageEvent).where(ProductUsageEvent.tenant_id == context.tenant_id)
            )
        ).all()
        assert len(events) == 1 and events[0].event_type == "consume"


async def test_stale_worker_cannot_finish_or_consume_task(metered_agent):
    sessions, context = metered_agent
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(),
        model_settings=ModelSettings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    created = await service.create(
        principal=context.principal, idempotency_key="stale-task", request=_request(context)
    )
    runtime = JobRuntimeService(session_factory=sessions)
    claim = await runtime.claim(job_id=created.job_id, worker_id="stale-worker")
    execution = await SqlAlchemyAgentExecutionLoader(sessions).load(
        claim, AgentExecutionPayload.model_validate(claim.payload)
    )
    settings = McpSettings()
    backend = DurableAgentGraphBackend(
        session_factory=sessions,
        context=execution,
        gateway=DeterministicGroundedGateway(),
        mcp_client=DatabaseBackedFakeMcpClient(session_factory=sessions, settings=settings),
        mcp_settings=settings,
        app_env=AppEnvironment.PRODUCTION,
    )
    await backend.prepare_segment()
    async with sessions.begin() as session:
        job = await session.get(Job, created.job_id)
        job.fencing_token += 1
    with pytest.raises(AgentGraphError):
        await backend.finalize({}, "succeeded")
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (0, 1)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProductUsageEvent)
                .where(ProductUsageEvent.tenant_id == context.tenant_id)
            )
            == 0
        )
