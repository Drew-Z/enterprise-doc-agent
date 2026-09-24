from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.jobs.models import Job, JobEvent, OutboxEvent
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesAttempt
from enterprise_doc_core.presales.schemas import CreatePacket, RequirementInput, SourceInput
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from tests.agent.test_agent_run_integration import _seed_agent_context
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.fixtures import add_chunk

pytestmark = pytest.mark.integration


@pytest.fixture
async def background(browser_db):
    sessions = browser_db.sessions
    context = await _seed_agent_context(sessions)
    other = await _seed_agent_context(sessions)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                id=uuid4(),
                tenant_id=context.tenant_id,
                plan_code="integration",
                version=1,
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(hours=1),
                provider_request_limit=100,
                created_at=now,
                updated_at=now,
            )
        )
    await add_chunk(
        sessions,
        context,
        context.document_version_id,
        context.generation_id,
        "Retention is 30 days.",
    )
    requests = []

    def unexpected_dispatch(request):
        requests.append(request)
        raise AssertionError("Enqueue must not call the provider")

    client = httpx.MockTransport(unexpected_dispatch)
    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://primary.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="test-model",
        ),
        transport=client,
    )
    settings = PresalesSettings(generation_enabled=True, background_generation_enabled=True)
    service = PresalesService(
        session_factory=sessions,
        retriever=HybridRetrievalService(
            session_factory=sessions, embedding_provider=HashEmbeddingProvider()
        ),
        gateway=gateway,
        settings=settings,
        usage_service=EntitlementUsageService(session_factory=sessions),
    )

    class Resolver:
        async def resolve(self, token):
            return context.principal if token == "owner" else other.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
    )
    payload = CreatePacket(
        title="Background generation",
        sources=[
            SourceInput(version_id=context.document_version_id, applicability="Current purchase")
        ],
        requirements=[RequirementInput(key="R1", text="Retention")],
    )
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as api:
                yield SimpleNamespace(
                    service=service,
                    sessions=sessions,
                    context=context,
                    other=other,
                    requests=requests,
                    client=client,
                    api=api,
                    payload=payload,
                    settings=settings,
                )
    finally:
        await client.aclose()


async def test_enqueue_is_durable_idempotent_and_returns_without_a_model_call(background):
    b = background
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "create"}
    response = await b.api.post(
        "/api/presales", json=b.payload.model_dump(mode="json"), headers=headers
    )
    assert response.status_code == 201, response.text
    packet = response.json()
    assert packet["generationMode"] == "background"
    packet_id, row_id = packet["id"], packet["rows"][0]["id"]
    url = f"/api/presales/{packet_id}/rows/{row_id}/generate"
    headers["Idempotency-Key"] = "generate-once"
    accepted = await b.api.post(url, headers=headers)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["rows"][0]["state"] == "queued"
    replay = await b.api.post(url, headers=headers)
    assert replay.status_code == 202
    refreshed = await b.api.get(f"/api/presales/{packet_id}", headers=headers)
    assert refreshed.json()["rows"] == replay.json()["rows"]
    assert accepted.headers["cache-control"] == "no-store"
    assert not b.requests
    assert (
        await b.api.post(url, headers={**headers, "Authorization": "Bearer other"})
    ).status_code == 404
    async with b.sessions() as session:
        operations = (
            await session.scalars(
                select(PresalesAttempt).where(PresalesAttempt.row_id == UUID(row_id))
            )
        ).all()
        assert len(operations) == 1
        operation = operations[0]
        jobs = (
            await session.scalars(
                select(Job).where(
                    Job.tenant_id == b.context.tenant_id, Job.type == "presales.generate"
                )
            )
        ).all()
        assert len(jobs) == 1 and jobs[0].id == operation.job_id
        assert jobs[0].payload["operation_id"] == str(operation.id)
        assert (
            await session.scalars(select(JobEvent).where(JobEvent.job_id == jobs[0].id))
        ).one().event_type == "job.created"
        assert not (
            await session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == jobs[0].id))
        ).all()
        reservation = (
            await session.scalars(
                select(UsageReservation).where(UsageReservation.operation_id == operation.id)
            )
        ).one()
        assert reservation.state == "reserved"
        assert operation.provider_request_count == 0


async def test_disabled_background_rejects_batch_before_any_operation_or_provider_call(background):
    b = background
    b.settings.background_generation_enabled = False
    packet = await b.service.create(b.context.principal, b.payload, "sync-packet")
    response = await b.api.post(
        f"/api/presales/{packet.id}/generate",
        json={"rowIds": [str(packet.rows[0].id)]},
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "sync-batch"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "presales_background_required"
    assert not b.requests
    assert packet.generation_mode == "synchronous"
    async with b.sessions() as session:
        assert not (await session.scalars(select(PresalesAttempt))).all()
        assert not (await session.scalars(select(UsageReservation))).all()


async def test_demo_queues_multiple_rows_and_counts_operations_once(background):
    from enterprise_doc_core.demo.models import DemoWorkspace

    b = background
    now = datetime.now(UTC)
    async with b.sessions.begin() as session:
        session.add(
            DemoWorkspace(
                tenant_id=b.context.tenant_id,
                actor_id=b.context.actor_id,
                created_at=now,
                expires_at=now + timedelta(hours=1),
                daily_attempt_limit=50,
                daily_workspace_limit=20,
            )
        )
    worker = configured_worker(
        b,
        lambda request: (
            httpx.Response(503)
            if request.url.host == "primary.invalid"
            else valid_response(request)
        ),
    )
    first, second = await enqueue(b, "first"), await enqueue(b, "second")
    await worker.run_once("worker")
    await worker.run_once("worker")
    for packet in (first, second):
        assert (await b.service.get(b.context.principal, packet.id)).rows[0].state == "drafted"
    async with b.sessions() as session:
        demo = await session.scalar(
            select(DemoWorkspace).where(DemoWorkspace.tenant_id == b.context.tenant_id)
        )
        assert demo.attempts_used == 2 and demo.active_attempt_id is None


def valid_response(request):
    payload = json.loads(json.loads(request.content)["messages"][1]["content"])
    return httpx.Response(
        200,
        json={
            "id": "test-response",
            "model": "test-model",
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "supported",
                                "answer": "资料规定保留期限为30天。",
                                "prerequisites": [],
                                "citations": [{"citationId": payload["evidence"][0]["citationId"]}],
                            },
                            ensure_ascii=False,
                        )
                    },
                }
            ],
        },
    )


async def test_worker_delivers_one_draft_and_one_charge_after_enqueue(background):
    from enterprise_doc_core.billing.models import UsageEvent
    from enterprise_doc_core.presales.background import BackgroundGeneration
    from enterprise_doc_core.presales.models import PresalesProviderCall

    b = background
    requests = []

    def respond(request):
        requests.append(request)
        return valid_response(request)

    gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.settings, transport=httpx.MockTransport(respond)
    )
    packet = await b.service.create(b.context.principal, b.payload, "create")
    queued = await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, "once")
    assert queued.rows[0].state == "queued"
    worker = BackgroundGeneration(b.service.generation, {"primary": gateway})
    assert await worker.run_once("worker-one")
    assert not await worker.run_once("worker-two")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "drafted"
    assert result.rows[0].draft.citations[0].excerpt == "Retention is 30 days."
    assert len(requests) == 1
    operation_id = result.rows[0].attempts[0].id
    async with b.sessions() as session:
        calls = (
            await session.scalars(
                select(PresalesProviderCall).where(
                    PresalesProviderCall.operation_id == operation_id
                )
            )
        ).all()
        assert len(calls) == 1 and calls[0].state == "succeeded"
        assert calls[0].usage["total_tokens"] == 50
        reservation = (
            await session.scalars(
                select(UsageReservation).where(UsageReservation.operation_id == operation_id)
            )
        ).one()
        assert reservation.state == "consumed"
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation_id))
        ).all()
        assert len([e for e in events if e.event_type == "consume"]) == 1


@pytest.mark.parametrize("fault", ["503", "error_envelope", "timeout"])
async def test_transient_failure_switches_once_and_keeps_two_internal_calls(background, fault):
    from enterprise_doc_core.presales.background import BackgroundGeneration
    from enterprise_doc_core.presales.models import PresalesProviderCall

    b = background
    b.settings.automatic_failover_enabled = True
    dispatched = []

    def respond(request):
        dispatched.append(request.url.host)
        if request.url.host == "primary.invalid":
            if fault == "timeout":
                raise httpx.ReadTimeout("controlled timeout", request=request)
            if fault == "error_envelope":
                return httpx.Response(
                    200, json={"error": {"type": "upstream_error", "message": "private diagnostic"}}
                )
            return httpx.Response(503)
        return valid_response(request)

    primary = b.service.generation.gateway.settings
    gateways = {
        name: OpenAICompatiblePresalesGateway(
            primary.model_copy(update={"base_url": f"https://{name}.invalid/v1"}),
            transport=httpx.MockTransport(respond),
        )
        for name in ("primary", "fallback")
    }
    packet = await b.service.create(b.context.principal, b.payload, "create")
    queued = await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, "once")
    worker = BackgroundGeneration(b.service.generation, gateways)
    assert await worker.run_once("worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "drafted", result.model_dump()
    assert dispatched == ["primary.invalid", "fallback.invalid"]
    assert result.rows[0].attempts[0].provider_request_count == 2
    assert not await worker.run_once("worker")
    async with b.sessions() as session:
        calls = (
            await session.scalars(
                select(PresalesProviderCall)
                .where(PresalesProviderCall.operation_id == queued.rows[0].attempts[0].id)
                .order_by(PresalesProviderCall.number)
            )
        ).all()
        assert [c.state for c in calls] == ["failed", "succeeded"]
        assert calls[0].usage is None
        assert calls[1].usage["total_tokens"] == 50
        assert "private diagnostic" not in str(result.model_dump())


def configured_worker(b, respond, **settings):
    from enterprise_doc_core.presales.background import BackgroundGeneration

    b.service.generation.settings = b.settings.model_copy(
        update={"automatic_failover_enabled": True, **settings}
    )
    primary = b.service.generation.gateway.settings
    gateways = {
        name: OpenAICompatiblePresalesGateway(
            primary.model_copy(update={"base_url": f"https://{name}.invalid/v1"}),
            transport=httpx.MockTransport(respond),
        )
        for name in ("primary", "fallback")
    }
    return BackgroundGeneration(b.service.generation, gateways)


async def enqueue(b, key="once"):
    packet = await b.service.create(b.context.principal, b.payload, f"create-{key}")
    return await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, key)


@pytest.mark.parametrize(
    "fault", ["both_fail", "invalid_citation", "invalid_output", "credentials"]
)
async def test_terminal_errors_release_reservation_without_extra_sampling(background, fault):
    from enterprise_doc_core.presales.models import PresalesProviderCall

    b = background
    dispatched = []

    def respond(request):
        dispatched.append(request.url.host)
        if fault == "both_fail":
            return httpx.Response(503)
        if fault == "credentials":
            return httpx.Response(401)
        response = valid_response(request).json()
        draft = json.loads(response["choices"][0]["message"]["content"])
        if fault == "invalid_citation":
            draft["citations"] = [{"citationId": "invented"}]
        else:
            draft.pop("prerequisites")
        response["choices"][0]["message"]["content"] = json.dumps(draft)
        return httpx.Response(200, json=response)

    worker = configured_worker(b, respond)
    packet = await enqueue(b)
    await worker.run_once("worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "failed" and result.rows[0].draft is None
    assert len(dispatched) == (2 if fault == "both_fail" else 1)
    await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, "once")
    assert not await worker.run_once("worker")
    async with b.sessions() as session:
        op_id = packet.rows[0].attempts[0].id
        assert (
            await session.scalar(
                select(UsageReservation).where(UsageReservation.operation_id == op_id)
            )
        ).state == "released"
        calls = (
            await session.scalars(
                select(PresalesProviderCall).where(PresalesProviderCall.operation_id == op_id)
            )
        ).all()
        if fault in {"invalid_citation", "invalid_output"}:
            assert calls[0].usage["total_tokens"] == 50


async def test_shutdown_recovers_remaining_route_and_preserves_unknown_usage(background):
    from enterprise_doc_core.presales.models import PresalesProviderCall
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    entered = asyncio.Event()
    dispatched = []

    async def respond(request):
        dispatched.append(request.url.host)
        if request.url.host == "primary.invalid":
            entered.set()
            await asyncio.Event().wait()
        return valid_response(request)

    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock
    worker = configured_worker(b, respond)
    packet = await enqueue(b)
    task = asyncio.create_task(worker.run_once("old-worker"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, packet.rows[0].attempts[0].id)
        deadline = operation.deadline_at
    clock.advance(31)
    assert await worker.run_once("new-worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "drafted"
    assert dispatched == ["primary.invalid", "fallback.invalid"]
    assert result.rows[0].attempts[0].usage is None
    assert result.rows[0].attempts[0].provider_request_count is None
    assert result.rows[0].attempts[0].deadline_at == deadline
    async with b.sessions() as session:
        calls = (
            await session.scalars(
                select(PresalesProviderCall).order_by(PresalesProviderCall.number)
            )
        ).all()
        assert [c.state for c in calls] == ["unknown", "succeeded"]


async def test_unhealthy_route_is_skipped_and_global_budget_is_not_multiplied(background):
    b = background
    dispatched = []

    def respond(request):
        dispatched.append(request.url.host)
        return (
            httpx.Response(503)
            if request.url.host == "primary.invalid"
            else valid_response(request)
        )

    worker = configured_worker(b, respond, route_failure_threshold=1, daily_dispatch_limit=3)
    first = await enqueue(b, "first")
    await worker.run_once("worker")
    assert (await b.service.get(b.context.principal, first.id)).rows[0].state == "drafted"
    second = await enqueue(b, "second")
    await worker.run_once("worker")
    assert (await b.service.get(b.context.principal, second.id)).rows[0].state == "drafted"
    assert dispatched == ["primary.invalid", "fallback.invalid", "fallback.invalid"]
    third = await enqueue(b, "third")
    await worker.run_once("worker")
    result = await b.service.get(b.context.principal, third.id)
    assert result.rows[0].state == "failed"
    assert result.rows[0].attempts[0].error_code == "presales_dispatch_budget"
    assert len(dispatched) == 3


@pytest.mark.parametrize("change", ["membership", "tenant", "source", "queue_expiry", "cancel"])
async def test_queue_revalidates_access_and_releases_unused_reservation(background, change):
    from enterprise_doc_core.documents.models import DocumentVersion
    from enterprise_doc_core.identity import Membership, Tenant
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock
    worker = configured_worker(
        b, lambda request: pytest.fail("No dispatch after access/deadline change")
    )
    packet = await enqueue(b)
    op_id = packet.rows[0].attempts[0].id
    async with b.sessions.begin() as session:
        if change == "membership":
            membership = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == b.context.tenant_id,
                    Membership.user_id == b.context.actor_id,
                )
            )
            membership.is_active = False
        elif change == "tenant":
            (await session.get(Tenant, b.context.tenant_id)).is_active = False
        elif change == "source":
            (await session.get(DocumentVersion, b.context.document_version_id)).status = "failed"
        job_id = (await session.get(PresalesAttempt, op_id)).job_id
    if change == "queue_expiry":
        clock.advance(901)
    if change == "cancel":
        await worker.runtime.cancel(
            job_id=job_id, tenant_id=b.context.tenant_id, actor_id=b.context.actor_id
        )
    await worker.run_once("worker")
    async with b.sessions() as session:
        op = await session.get(PresalesAttempt, op_id)
        assert op.state in {"failed", "expired"}
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == op_id)
        )
        assert reservation.state == "released"
    assert not b.requests


async def test_generic_job_retry_cannot_bypass_dispatch_budget_or_create_celery_work(background):
    from enterprise_doc_core.jobs.service import JobNotClaimable

    b = background
    worker = configured_worker(b, lambda _: httpx.Response(503))
    packet = await enqueue(b)
    await worker.run_once("worker")
    async with b.sessions() as session:
        job_id = (await session.get(PresalesAttempt, packet.rows[0].attempts[0].id)).job_id
    with pytest.raises(JobNotClaimable):
        await worker.runtime.retry_dead(
            job_id=job_id, tenant_id=b.context.tenant_id, actor_id=b.context.actor_id
        )
    async with b.sessions() as session:
        assert not (
            await session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
        ).all()


async def test_batch_enqueues_valid_rows_after_a_row_rejection_and_replays_once(background):
    b = background
    payload = b.payload.model_copy(
        update={
            "requirements": [
                RequirementInput(key="R1", text="Retention"),
                RequirementInput(key="R2", text="Retention period"),
            ]
        }
    )
    packet = await b.service.create(b.context.principal, payload, "batch-create")
    row_ids = [str(packet.rows[0].id), str(uuid4()), str(packet.rows[1].id)]
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "batch-once"}
    for _ in range(2):
        response = await b.api.post(
            f"/api/presales/{packet.id}/generate", json={"rowIds": row_ids}, headers=headers
        )
        assert response.status_code == 202, response.text
        assert [r["state"] for r in response.json()["packet"]["rows"]] == ["queued", "queued"]
        assert response.json()["rejected"] == [{"rowId": row_ids[1], "code": "presales_not_found"}]
    async with b.sessions() as session:
        assert len((await session.scalars(select(PresalesAttempt))).all()) == 2
    assert not b.requests


async def test_background_migration_round_trip_and_refuses_discarding_history(browser_db):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    from enterprise_doc_core.db.metadata import metadata

    module = import_module(
        "enterprise_doc_core.db.migrations.versions.20260924_0028_presales_background"
    )

    def migrate(connection):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            module.downgrade()
            assert "job_id" not in {
                c["name"] for c in inspect(connection).get_columns("presales_attempts")
            }
            module.upgrade()
            assert "presales_provider_calls" in inspect(connection).get_table_names()
            savepoint = connection.begin_nested()
            try:
                connection.execute(
                    text(
                        "INSERT INTO presales_dispatch_days (day, dispatched) "
                        "VALUES ('2026-09-24', 1)"
                    )
                )
                with pytest.raises(RuntimeError, match="Preserve background generation history"):
                    module.downgrade()
                assert "presales_provider_calls" in inspect(connection).get_table_names()
            finally:
                savepoint.rollback()

    async with browser_db.engine.begin() as connection:
        await connection.run_sync(migrate)


async def test_old_lease_cannot_overwrite_the_recovered_draft(background):
    from enterprise_doc_core.jobs.service import JobLeaseLost
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if request.url.host == "primary.invalid":
            entered.set()
            await release.wait()
        return valid_response(request)

    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock
    worker = configured_worker(b, respond)
    packet = await enqueue(b)
    async with b.sessions() as session:
        job_id = (await session.get(PresalesAttempt, packet.rows[0].attempts[0].id)).job_id
    old_claim = await worker.runtime.claim(job_id=job_id, worker_id="old")
    old_work = asyncio.create_task(worker.execute(old_claim))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        clock.advance(31)
        new_claim = await worker.runtime.claim(job_id=job_id, worker_id="new")
        await worker.execute(new_claim)
        before = await b.service.get(b.context.principal, packet.id)
        release.set()
        with pytest.raises(JobLeaseLost):
            await old_work
        after = await b.service.get(b.context.principal, packet.id)
        assert after == before and after.rows[0].revision == 1
    finally:
        old_work.cancel()
        await asyncio.gather(old_work, return_exceptions=True)


async def test_two_unobserved_dispatches_never_get_a_third_call_after_restart(background):
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    entered = asyncio.Event()
    dispatched = []

    async def respond(request):
        dispatched.append(request.url.host)
        entered.set()
        await asyncio.Event().wait()

    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock
    worker = configured_worker(b, respond)
    packet = await enqueue(b)
    async with b.sessions() as session:
        job_id = (await session.get(PresalesAttempt, packet.rows[0].attempts[0].id)).job_id
    for number in range(2):
        entered.clear()
        claim = await worker.runtime.claim(job_id=job_id, worker_id=str(number))
        task = asyncio.create_task(worker.execute(claim))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        clock.advance(31)
    assert await worker.run_once("third-worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "failed" and result.rows[0].draft is None
    assert dispatched == ["primary.invalid", "fallback.invalid"]
    async with b.sessions() as session:
        reservation = await session.scalar(
            select(UsageReservation).where(
                UsageReservation.operation_id == packet.rows[0].attempts[0].id
            )
        )
        assert reservation.state == "released"


async def test_cooldown_permits_only_one_probe_and_recovers_the_route(background):
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    entered, release = asyncio.Event(), asyncio.Event()
    phase, dispatched = ["outage"], []

    async def respond(request):
        dispatched.append(request.url.host)
        if phase[0] == "outage":
            return httpx.Response(503)
        if request.url.host == "primary.invalid":
            entered.set()
            await release.wait()
        return valid_response(request)

    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock
    worker = configured_worker(b, respond, route_failure_threshold=1)
    await enqueue(b, "outage")
    await worker.run_once("initial")
    clock.advance(31)
    phase[0] = "probe"
    await enqueue(b, "probe")
    probe = asyncio.create_task(worker.run_once("probe"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await enqueue(b, "concurrent")
        await worker.run_once("concurrent")
        assert dispatched.count("primary.invalid") == 2
        release.set()
        await probe
        await enqueue(b, "healthy")
        await worker.run_once("healthy")
        assert dispatched.count("primary.invalid") == 3
    finally:
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)


async def test_observed_but_expired_response_keeps_usage_without_saving_a_draft(background):
    from enterprise_doc_core.presales.models import PresalesProviderCall
    from tests.agent.test_agent_run_integration import MutableClock

    b = background
    clock = MutableClock(datetime.now(UTC))
    b.service.generation.clock = clock

    def respond(request):
        clock.advance(91)
        return valid_response(request)

    worker = configured_worker(b, respond)
    packet = await enqueue(b)
    # Simulate a long but healthy worker lease; operation deadline still applies.
    worker.runtime.lease_seconds = 180
    await worker.run_once("worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].state == "failed" and result.rows[0].draft is None
    async with b.sessions() as session:
        call = (await session.scalars(select(PresalesProviderCall))).one()
        assert call.usage["total_tokens"] == 50
        assert call.provider_response_id == "test-response"


async def test_validation_before_http_does_not_count_as_a_provider_request(background):
    from enterprise_doc_core.presales.models import PresalesProviderCall

    b = background
    worker = configured_worker(b, lambda _: httpx.Response(503))
    worker.gateways["fallback"] = OpenAICompatiblePresalesGateway(
        ModelSettings(provider=ModelProvider.DETERMINISTIC)
    )
    packet = await enqueue(b)
    await worker.run_once("worker")
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].attempts[0].provider_request_count == 1
    async with b.sessions() as session:
        calls = (
            await session.scalars(
                select(PresalesProviderCall).order_by(PresalesProviderCall.number)
            )
        ).all()
        assert [c.state for c in calls] == ["failed", "not_sent"]
