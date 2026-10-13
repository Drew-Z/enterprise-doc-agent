import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, select
from tests.agent.test_agent_run_integration import _request, _seed_agent_context
from tests.agent.test_graph_worker_integration import DatabaseBackedFakeMcpClient
from tests.mcp.test_tool_service_integration import FixedRetrieval, MemoryArtifactStore

from enterprise_doc_core.agents import (
    AgentGraphError,
    AgentRun,
    AgentRunService,
    AgentRunTaskType,
    AgentToolService,
    BehaviorVersions,
    DeterministicGroundedGateway,
    GroundedModelRequest,
    verify_execution_context,
)
from enterprise_doc_core.agents.artifact_service import AgentArtifactService
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
from enterprise_doc_core.billing.reconciliation import UsageReconciliationService
from enterprise_doc_core.billing.reconciliation_contracts import UsageExportWindow
from enterprise_doc_core.billing.service import EntitlementUsageService
from enterprise_doc_core.config import (
    AgentSettings,
    AppEnvironment,
    McpSettings,
    ModelSettings,
    ProviderUsageSettings,
)
from enterprise_doc_core.documents.models import DEFAULT_EMBEDDING_DIMENSION, DocumentChunk
from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.identity.models import Tenant, User
from enterprise_doc_core.jobs import Job, JobRuntimeService
from enterprise_doc_worker.agent_backend import DurableAgentGraphBackend
from enterprise_doc_worker.agent_handler import (
    AgentExecutionPayload,
    SqlAlchemyAgentExecutionLoader,
    agent_failure_lock_key,
    project_agent_run_failure,
)
from enterprise_doc_worker.agents import build_durable_agent_handler
from enterprise_doc_worker.handler import classify_job_error
from enterprise_doc_worker.mcp_client import McpStdioClient
from enterprise_doc_worker.queue import JobDeliveryConsumer, JobMessage

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


@pytest.fixture
async def grounded_metered_agent(metered_agent):
    sessions, context = metered_agent
    source = "Payment is due within 30 days after acceptance."
    chunk_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            DocumentChunk(
                id=chunk_id,
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
    return sessions, context, chunk_id, source


def _metered_model_settings():
    return ModelSettings(
        provider="openai_compatible",
        base_url="https://provider.test/v1",
        api_key="test-only",
        model_name="accounting-test",
    )


class ServiceBackedMcpClient(McpStdioClient):
    """Keep tool policy, persistence and artifact construction; bypass only stdio."""

    def __init__(self, service, settings):
        self.service = service
        self.settings = settings

    async def search_document(self, *, context_token, request):
        context = verify_execution_context(str(context_token), self.settings.signing_secret)
        return await self.service.search_document(context, request)

    async def create_draft_artifact(self, *, context_token, request):
        context = verify_execution_context(str(context_token), self.settings.signing_secret)
        return await self.service.create_draft_artifact(context, request)


def _metered_consumer(sessions, client, *, call_limit, mcp_client=None):
    model_settings = _metered_model_settings()
    mcp_settings = McpSettings()
    return JobDeliveryConsumer(
        runtime=JobRuntimeService(
            session_factory=sessions,
            failure_lock_key=agent_failure_lock_key,
            failure_projector=project_agent_run_failure,
            jitter=lambda _: 0.0,
        ),
        worker_id="metered-worker",
        handler=build_durable_agent_handler(
            session_factory=sessions,
            model_settings=model_settings,
            mcp_settings=mcp_settings,
            mcp_client=mcp_client
            or DatabaseBackedFakeMcpClient(session_factory=sessions, settings=mcp_settings),
            gateway=OpenAICompatibleChatGateway(
                settings=model_settings, client=client, require_metering=True
            ),
            provider_usage_settings=ProviderUsageSettings(agent_call_limit=call_limit),
            checkpointer=InMemorySaver(),
            app_env=AppEnvironment.PRODUCTION,
        ),
        classify_error=classify_job_error,
    )


@pytest.mark.parametrize("repair", [False, True], ids=["direct", "schema-repair"])
async def test_formal_worker_success_consumes_one_task_with_terminal_event(
    grounded_metered_agent, repair
):
    sessions, context, chunk_id, source = grounded_metered_agent
    started = datetime.now(UTC)
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(execution_max_attempts=1),
        model_settings=_metered_model_settings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    created = await service.create(
        principal=context.principal, idempotency_key="complete-task", request=_request(context)
    )
    async with sessions() as session:
        original_quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (original_quota.units_used, original_quota.units_reserved) == (0, 1)
    calls = []

    def provider(request):
        assert request.url == "https://provider.test/v1/chat/completions"
        calls.append(json.loads(request.content))
        answer = {
            "outcome": "answer",
            "task_type": "question_answer",
            "answer_text": source,
            "structured_fields": None,
            "citations": [
                {
                    "chunk_id": str(chunk_id),
                    "document_version_id": str(context.document_version_id),
                    "excerpt": source,
                }
            ],
            "risk_hint": "low",
            "refusal_reason": None,
        }
        content = "malformed" if repair and len(calls) == 1 else json.dumps(answer)
        return httpx.Response(
            200,
            headers={"x-request-id": f"req-{len(calls)}"},
            json={
                "id": f"resp-{len(calls)}",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38},
            },
        )

    expected_calls = 2 if repair else 1
    store = MemoryArtifactStore()
    tools = AgentToolService(
        session_factory=sessions,
        retrieval_service=FixedRetrieval(
            RetrievalCandidate(
                chunk_id=chunk_id,
                tenant_id=context.tenant_id,
                document_version_id=context.document_version_id,
                generation_id=context.generation_id,
                text=source,
                page_number=1,
                heading="Payment",
                start_offset=0,
                end_offset=len(source),
                source_filename=context.filename,
                score=0.9,
            )
        ),
        artifact_store=store,
        app_env=AppEnvironment.PRODUCTION,
    )
    message = JobMessage(job_id=created.job_id, tenant_id=context.tenant_id, event_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        consumer = _metered_consumer(
            sessions,
            client,
            call_limit=expected_calls,
            mcp_client=ServiceBackedMcpClient(tools, McpSettings()),
        )
        assert await consumer.handle(message) == "succeeded"
        assert await consumer.handle(message) == "duplicate_or_not_claimable"
        replay = await service.create(
            principal=context.principal, idempotency_key="complete-task", request=_request(context)
        )
        assert (
            replay.replayed and replay.run_id == created.run_id and replay.job_id == created.job_id
        )
        for _ in range(2):
            status = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
            assert status.status == "succeeded"
            artifacts = await AgentArtifactService(
                session_factory=sessions, artifact_store=store
            ).list_for_run(
                run_id=created.run_id, tenant_id=context.tenant_id, actor_id=context.actor_id
            )
            assert len(artifacts) == 1 and artifacts[0].run_id == created.run_id
        with pytest.raises(AgentRunError) as rejected:
            await service.create(
                principal=context.principal,
                idempotency_key="new-over-quota",
                request=_request(context),
            )
        assert rejected.value.code == "agent_usage_limit"
    assert len(calls) == expected_calls
    assert store.put_calls == 1
    assert len(store.objects) == 1
    body, _, _ = next(iter(store.objects.values()))
    assert hashlib.sha256(body).hexdigest() == artifacts[0].content_sha256
    assert json.loads(body)["answer_text"] == source
    assert all(c["model"] == "accounting-test" for c in calls)
    usage = await EntitlementUsageService(session_factory=sessions).summary(
        tenant_id=context.tenant_id
    )
    assert usage.model_calls.calls == expected_calls
    assert usage.model_calls.unknown_cost_calls == expected_calls
    assert usage.model_calls.usage_known_calls == expected_calls
    assert usage.model_calls.known_total_tokens == 38 * expected_calls
    assert usage.cost_status == "unknown" and usage.provider_requests_used == 0
    exported = await UsageReconciliationService(session_factory=sessions).export(
        tenant_id=context.tenant_id,
        operator=PlatformEntitlementOperator("agent-test", "Controlled Agent accounting"),
        window=UsageExportWindow(start=started, end=datetime.now(UTC)),
    )
    assert len(exported.provider_calls) == expected_calls
    assert {c.operation_id for c in exported.provider_calls} == {created.run_id}
    assert exported.summary.known_total_tokens == 38 * expected_calls
    assert exported.summary.unknown_cost_records == expected_calls
    assert exported.summary.unresolved_records == 0
    assert len(exported.business_events) == 1
    event = exported.business_events[0]
    assert (event.operation_id, event.metric, event.event_type, event.quantity) == (
        created.run_id,
        "agent_task",
        "consume",
        1,
    )
    assert event.entitlement_id == original_quota.entitlement_id
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (1, 0)
        assert quota.id == original_quota.id
        events = (
            await session.scalars(
                select(ProductUsageEvent).where(ProductUsageEvent.tenant_id == context.tenant_id)
            )
        ).all()
        assert len(events) == 1 and events[0].event_type == "consume"
        reservation = await session.get(ProductUsageReservation, events[0].reservation_id)
        assert (reservation.operation_id, reservation.quota_id, reservation.state) == (
            created.run_id,
            original_quota.id,
            "consumed",
        )
        receipts = (
            await session.scalars(
                select(ProviderDispatch).where(
                    ProviderDispatch.tenant_id == context.tenant_id,
                    ProviderDispatch.operation_id == created.run_id,
                )
            )
        ).all()
        assert len(receipts) == expected_calls
        assert {r.provider_request_id for r in receipts} == {
            f"req-{i}" for i in range(1, expected_calls + 1)
        }
        assert {r.provider_response_id for r in receipts} == {
            f"resp-{i}" for i in range(1, expected_calls + 1)
        }
        assert all(r.kind == "agent" and r.state == "responded" for r in receipts)
        assert all(
            r.total_tokens == 38 and r.estimated_cost is None and r.currency is None
            for r in receipts
        )
        run = await session.get(AgentRun, created.run_id)
        assert (
            run.provider_request_count == expected_calls and run.total_tokens == 38 * expected_calls
        )
        assert run.repair_request_count == int(repair)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.tenant_id == context.tenant_id)
            )
            == 1
        )


@pytest.mark.parametrize(
    ("failure", "max_attempts", "state", "error_code"),
    [
        ("timeout", 1, "timeout", "model_timeout"),
        ("server", 1, "http_error", "model_server_error"),
        ("schema", 1, "responded", "provider_operation_budget_exhausted"),
        ("server", 2, "http_error", "provider_operation_budget_exhausted"),
    ],
    ids=["timeout", "http-503", "repair-budget", "retry-budget"],
)
async def test_failed_worker_releases_task_but_keeps_bounded_provider_receipt(
    grounded_metered_agent, failure, max_attempts, state, error_code
):
    sessions, context, _, _ = grounded_metered_agent
    started = datetime.now(UTC)
    service = AgentRunService(
        session_factory=sessions,
        agent_settings=AgentSettings(execution_max_attempts=max_attempts),
        model_settings=_metered_model_settings(),
        app_env=AppEnvironment.PRODUCTION,
    )
    created = await service.create(
        principal=context.principal, idempotency_key="failed-task", request=_request(context)
    )
    calls = []

    def provider(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream timeout detail", request=request)
        return httpx.Response(
            503 if failure == "server" else 200,
            headers={"x-request-id": "failed-req"},
            json={"id": "failed-resp", "choices": [{"message": {"content": "malformed"}}]},
        )

    message = JobMessage(job_id=created.job_id, tenant_id=context.tenant_id, event_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        consumer = _metered_consumer(sessions, client, call_limit=1)
        assert await consumer.handle(message) == "failed"
        if max_attempts == 2:
            async with sessions() as session:
                job = await session.get(Job, created.job_id)
                assert job.status == "retry_wait" and job.attempts == 1
                quota = await session.scalar(
                    select(ProductQuota).where(
                        ProductQuota.tenant_id == context.tenant_id,
                        ProductQuota.metric == "agent_task",
                    )
                )
                assert (quota.units_used, quota.units_reserved) == (0, 1)
            assert await consumer.handle(message) == "failed"
        assert await consumer.handle(message) == "duplicate_or_not_claimable"
        replay = await service.create(
            principal=context.principal, idempotency_key="failed-task", request=_request(context)
        )
        assert replay.replayed and replay.run_id == created.run_id
    assert len(calls) == 1  # Repair or job retry must be refused before a second HTTP request.
    status = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
    assert status.status == "failed" and status.error_code == error_code
    assert "private upstream" not in str(status)
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == context.tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (0, 0)
        reservation = await session.scalar(
            select(ProductUsageReservation).where(
                ProductUsageReservation.tenant_id == context.tenant_id,
                ProductUsageReservation.operation_id == created.run_id,
            )
        )
        assert reservation.state == "released" and reservation.quota_id == quota.id
        events = (
            await session.scalars(
                select(ProductUsageEvent).where(ProductUsageEvent.tenant_id == context.tenant_id)
            )
        ).all()
        assert len(events) == 1
        assert events[0].event_type == "release" and events[0].reservation_id == reservation.id
        job = await session.get(Job, created.job_id)
        assert job.status == "dead" and job.attempts == max_attempts
    usage = await EntitlementUsageService(session_factory=sessions).summary(
        tenant_id=context.tenant_id
    )
    assert usage.model_calls.calls == 1 and usage.model_calls.unknown_cost_calls == 1
    assert usage.model_calls.unresolved_calls == int(failure == "timeout")
    assert usage.model_calls.usage_known_calls == 0 and usage.cost_status == "unknown"
    exported = await UsageReconciliationService(session_factory=sessions).export(
        tenant_id=context.tenant_id,
        operator=PlatformEntitlementOperator("agent-test", "Controlled failure accounting"),
        window=UsageExportWindow(start=started, end=datetime.now(UTC)),
    )
    assert len(exported.provider_calls) == 1
    receipt = exported.provider_calls[0]
    assert receipt.operation_id == created.run_id and receipt.state == state
    assert receipt.estimated_cost is None and receipt.currency is None
    assert receipt.total_tokens is None
    assert receipt.provider_request_id == (None if failure == "timeout" else "failed-req")
    assert receipt.provider_response_id == (None if failure == "timeout" else "failed-resp")
    assert (
        exported.summary.unknown_cost_records == 1 and exported.summary.missing_token_records == 1
    )
    assert len(exported.business_events) == 1
    event = exported.business_events[0]
    assert (event.operation_id, event.metric, event.event_type, event.quantity) == (
        created.run_id,
        "agent_task",
        "release",
        1,
    )
    assert event.entitlement_id == quota.entitlement_id


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
