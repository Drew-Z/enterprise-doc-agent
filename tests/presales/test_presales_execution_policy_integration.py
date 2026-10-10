from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from enterprise_doc_core.billing.models import UsageEvent, UsageReservation
from enterprise_doc_core.config import ModelProvider
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import (
    PresalesAttempt,
    PresalesDispatchDay,
    PresalesProviderCall,
)
from enterprise_doc_core.presales.schemas import RequirementInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import (
    background as background,
)
from tests.presales.test_presales_background_integration import (
    valid_response,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def test_deep_policy_is_frozen_at_admission_and_survives_config_change_and_replay(background):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "policy-create")
    url = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "policy-once"}
    accepted = await b.api.post(url, headers=headers, json={"executionMode": "deep"})
    assert accepted.status_code == 202, accepted.text
    attempt = accepted.json()["rows"][0]["attempts"][0]
    policy = attempt["executionPolicy"]
    assert policy["mode"] == "deep" and policy["version"] == "presales.execution.v1"
    assert policy["rowTimeoutSeconds"] == 660
    assert policy["maxProviderRequests"] == 1
    assert policy["routes"][0]["modelName"] == "test-model"
    assert policy["routes"][0]["reasoningEffort"] == "high"
    assert policy["routes"][0]["timeoutSeconds"] == 300
    assert "test-only" not in json.dumps(policy) and "apiKey" not in json.dumps(policy)
    assert not b.requests

    # A restart uses different process settings but the same durable operation.
    b.settings.row_timeout_seconds = 5
    b.settings.queue_timeout_seconds = 30
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return valid_response(request)

    changed = b.service.generation.gateway.settings.model_copy(
        update={"model_name": "new-model", "reasoning_effort": "low", "timeout_seconds": 1}
    )
    gateway = OpenAICompatiblePresalesGateway(changed, transport=httpx.MockTransport(respond))
    b.service.generation.gateway = gateway
    replay = await b.api.post(url, headers=headers, json={"executionMode": "deep"})
    assert replay.status_code == 202, replay.text
    assert replay.json()["rows"][0]["attempts"][0] == attempt
    conflict = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "presales_idempotency_conflict"
    worker = BackgroundGeneration(b.service.generation, {"primary": gateway})
    assert await worker.run_once("policy-worker-one")
    assert not await worker.run_once("policy-worker-two")
    refreshed = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    row = refreshed.json()["rows"][0]
    assert row["state"] == "drafted", row
    assert row["attempts"][0]["executionPolicy"] == policy
    assert len(requests) == 1
    assert requests[0]["model"] == "test-model" and requests[0]["reasoning_effort"] == "high"
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, UUID(attempt["id"]))
        assert (operation.deadline_at - operation.started_at).total_seconds() == 660
        calls = (
            await session.scalars(
                select(PresalesProviderCall).where(
                    PresalesProviderCall.operation_id == operation.id
                )
            )
        ).all()
        assert len(calls) == 1 and calls[0].state == "succeeded"
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == operation.id)
        )
        assert reservation.state == "consumed"
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation.id))
        ).all()
        assert len(events) == 1


async def test_recovery_keeps_original_deadline_routes_and_two_dispatch_limit(background):
    b = background
    clock = [datetime.now(UTC)]
    b.service.clock = b.service.generation.clock = lambda: clock[0]
    b.settings.automatic_failover_enabled = True
    root = b.service.generation.gateway.source_settings.model_copy(
        update={
            "fallback_provider": ModelProvider.OPENAI_COMPATIBLE,
            "fallback_base_url": "https://fallback.invalid/v1",
            "fallback_api_key": b.service.generation.gateway.settings.api_key,
            "fallback_model_name": "fallback-model",
        }
    )
    b.service.generation.gateway = OpenAICompatiblePresalesGateway(root)
    packet = await b.service.create(b.context.principal, b.payload, "recover-policy")
    url = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "recover-once"}
    admitted = await b.api.post(url, headers=headers, json={"executionMode": "deep"})
    assert admitted.status_code == 202, admitted.text
    snapshot = admitted.json()["rows"][0]["attempts"][0]["executionPolicy"]
    operation_id = UUID(admitted.json()["rows"][0]["attempts"][0]["id"])
    entered = asyncio.Event()
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append((request.url.host, body))
        if request.url.host == "primary.invalid":
            entered.set()
            await asyncio.Event().wait()
        value = valid_response(request).json()
        value["model"] = body["model"]
        return httpx.Response(200, json=value)

    transport = httpx.MockTransport(respond)
    routes = {
        name: OpenAICompatiblePresalesGateway(
            root,
            presales_settings=b.settings.model_copy(update={"model_route": name}),
            transport=transport,
        )
        for name in ["primary", "fallback"]
    }
    worker = BackgroundGeneration(b.service.generation, routes)
    running = asyncio.create_task(worker.run_once("interrupted-policy-worker"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    async with b.sessions() as session:
        started = await session.get(PresalesAttempt, operation_id)
        deadline, began = started.deadline_at, started.started_at
    clock[0] += timedelta(seconds=31)
    b.settings.automatic_failover_enabled = False
    b.settings.row_timeout_seconds = 5
    b.settings.model_route = "fallback"
    changed = root.model_copy(
        update={
            "model_name": "changed-primary",
            "fallback_model_name": "changed-fallback",
            "reasoning_effort": "low",
            "fallback_reasoning_effort": "low",
        }
    )
    recovered_routes = {
        name: OpenAICompatiblePresalesGateway(
            changed,
            presales_settings=b.settings.model_copy(update={"model_route": name}),
            transport=transport,
        )
        for name in ["primary", "fallback"]
    }
    recovered = BackgroundGeneration(b.service.generation, recovered_routes)
    assert await recovered.run_once("recovered-policy-worker")
    assert not await recovered.run_once("no-third-dispatch")
    result = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    row = result.json()["rows"][0]
    assert row["state"] == "drafted", row
    assert row["attempts"][0]["executionPolicy"] == snapshot
    assert [body["model"] for _, body in requests] == ["test-model", "fallback-model"]
    assert all(body["reasoning_effort"] == "high" for _, body in requests)
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, operation_id)
        assert (operation.started_at, operation.deadline_at) == (began, deadline)
        calls = (
            await session.scalars(
                select(PresalesProviderCall)
                .where(PresalesProviderCall.operation_id == operation_id)
                .order_by(PresalesProviderCall.number)
            )
        ).all()
        assert [call.state for call in calls] == ["unknown", "succeeded"]
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation_id))
        ).all()
        assert len(events) == 1


@pytest.mark.parametrize("drift", ["endpoint", "historical_prompt"])
async def test_policy_identity_drift_fails_without_sending_or_consuming_a_success(
    background, drift
):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "drift-policy")
    url = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "drift-once"}
    admitted = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert admitted.status_code == 202, admitted.text
    operation_id = UUID(admitted.json()["rows"][0]["attempts"][0]["id"])
    changed = b.service.generation.gateway
    if drift == "endpoint":
        changed = OpenAICompatiblePresalesGateway(
            changed.settings.model_copy(update={"base_url": "https://changed.invalid/v1"}),
            transport=b.client,
        )
    else:
        # Seed a valid historical policy whose prompt cannot be served by this build.
        async with b.sessions.begin() as session:
            operation = await session.get(PresalesAttempt, operation_id)
            historical = json.loads(json.dumps(operation.execution_policy))
            historical["routes"][0]["prompt_sha256"] = "0" * 64
            operation.execution_policy = historical
    worker = BackgroundGeneration(b.service.generation, {"primary": changed})
    assert await worker.run_once("drift-worker")
    result = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    row = result.json()["rows"][0]
    assert row["state"] == "failed" and row["draft"] is None
    assert row["attempts"][0]["errorCode"] == "presales_execution_policy_unavailable"
    assert not b.requests
    async with b.sessions() as session:
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == operation_id)
        )
        assert reservation.state == "released"
        assert not (
            await session.scalars(
                select(PresalesProviderCall).where(
                    PresalesProviderCall.operation_id == operation_id
                )
            )
        ).all()
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation_id))
        ).all()
        assert [event.event_type for event in events] == ["release"]


@pytest.mark.parametrize("original_limit,current_limit", [(1, 200), (200, 1)])
async def test_original_and_current_daily_caps_both_remain_enforced(
    background, original_limit, current_limit
):
    b = background
    clock = datetime.now(UTC)
    b.service.generation.clock = lambda: clock
    b.settings.daily_dispatch_limit = original_limit
    packet = await b.service.create(b.context.principal, b.payload, "limited-policy")
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "limited-once"}
    admitted = await b.api.post(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
        headers=headers,
        json={"executionMode": "deep"},
    )
    assert admitted.status_code == 202, admitted.text
    operation_id = UUID(admitted.json()["rows"][0]["attempts"][0]["id"])
    async with b.sessions.begin() as session:
        session.add(PresalesDispatchDay(day=clock.date(), dispatched=1))
    b.settings.daily_dispatch_limit = current_limit
    worker = BackgroundGeneration(b.service.generation, {"primary": b.service.generation.gateway})
    assert await worker.run_once("limited-worker")
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, operation_id)
        assert operation.error_code == "presales_dispatch_budget"
        assert (await session.get(PresalesDispatchDay, clock.date())).dispatched == 1
        assert not (
            await session.scalars(
                select(PresalesProviderCall).where(
                    PresalesProviderCall.operation_id == operation_id
                )
            )
        ).all()
    assert not b.requests


@pytest.mark.parametrize(
    "case", ["invalid", "extra_budget", "sync_deep", "foreign", "missing_fallback"]
)
async def test_rejected_mode_requests_never_admit_or_reserve(background, case):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "rejected-mode")
    body = {"executionMode": "deep"}
    token = "owner"
    expected = 422
    if case == "invalid":
        body["executionMode"] = "unlimited"
    elif case == "extra_budget":
        body["rowTimeoutSeconds"] = 99999
    elif case == "sync_deep":
        b.settings.background_generation_enabled = False
        expected = 409
    elif case == "missing_fallback":
        b.settings.automatic_failover_enabled = True
        expected = 503
    else:
        token, expected = "other", 404
    rejected = await b.api.post(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "rejected-once"},
        json=body,
    )
    assert rejected.status_code == expected, rejected.text
    async with b.sessions() as session:
        assert not (await session.scalars(select(PresalesAttempt))).all()
        assert not (await session.scalars(select(UsageReservation))).all()
        assert not (await session.scalars(select(Job).where(Job.type == "presales.generate"))).all()
    assert not b.requests


async def test_synchronous_auto_records_effective_policy_and_settles_once(background):
    b = background
    b.settings.background_generation_enabled = False
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return valid_response(request)

    gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.source_settings.model_copy(
            update={"reasoning_effort": "medium"}
        ),
        presales_settings=b.settings,
        transport=httpx.MockTransport(respond),
    )
    b.service.generation.gateway = gateway
    packet = await b.service.create(b.context.principal, b.payload, "sync-auto")
    url = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "sync-auto-once"}
    generated = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert generated.status_code == 200, generated.text
    result = generated.json()
    assert result["availableExecutionModes"] == ["auto"]
    row = result["rows"][0]
    assert row["state"] == "drafted", row
    attempt = row["attempts"][0]
    policy = attempt["executionPolicy"]
    assert policy["mode"] == "auto" and policy["maxProviderRequests"] == 1
    assert policy["rowTimeoutSeconds"] == b.settings.row_timeout_seconds
    assert policy["routes"][0]["reasoningEffort"] == "medium"
    assert policy["routes"][0]["timeoutSeconds"] == gateway.request_timeout_seconds
    assert len(requests) == 1 and requests[0]["reasoning_effort"] == "medium"
    replay = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert replay.status_code == 200 and replay.json()["rows"] == result["rows"]
    conflict = await b.api.post(url, headers=headers)
    assert conflict.status_code == 409
    assert len(requests) == 1
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, UUID(attempt["id"]))
        assert operation.job_id is None
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == operation.id)
        )
        assert reservation.state == "consumed"
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation.id))
        ).all()
        assert len(events) == 1


async def test_batch_modes_and_per_row_replay_retain_original_policies(background):
    b = background
    b.payload.requirements.append(RequirementInput(key="R2", text="Second retention requirement"))
    packet = await b.service.create(b.context.principal, b.payload, "batch-policy")
    url = f"/api/presales/{packet.id}/generate?response=receipt"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "batch-once"}
    payload = {"rowIds": [str(row.id) for row in packet.rows], "executionMode": "deep"}
    admitted = await b.api.post(url, headers=headers, json=payload)
    assert admitted.status_code == 202, admitted.text
    assert len(admitted.json()["admissions"]) == 2 and not admitted.json()["rejected"]
    b.settings.row_timeout_seconds = 800
    replay = await b.api.post(url, headers=headers, json=payload)
    assert replay.status_code == 200 and all(
        row["disposition"] == "replayed" for row in replay.json()["admissions"]
    )
    changed = await b.api.post(url, headers=headers, json={**payload, "executionMode": "auto"})
    assert changed.status_code == 200 and not changed.json()["admissions"]
    assert [row["code"] for row in changed.json()["rejected"]] == [
        "presales_idempotency_conflict"
    ] * 2
    current = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    assert all(
        row["attempts"][0]["executionPolicy"]["rowTimeoutSeconds"] == 660
        for row in current.json()["rows"]
    )
    async with b.sessions() as session:
        assert len((await session.scalars(select(PresalesAttempt))).all()) == 2
        assert len((await session.scalars(select(UsageReservation))).all()) == 2
    assert not b.requests


async def test_policy_migration_preserves_legacy_rows_and_refuses_history_loss(background):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "migration-policy")
    old = await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, "old-client")
    assert old.rows[0].attempts[0].execution_policy is None
    path = (
        ROOT
        / "packages/core/src/enterprise_doc_core/db/migrations/versions"
        / "20261008_0034_presales_execution_policy.py"
    )
    spec = importlib.util.spec_from_file_location("policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection, direction):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            getattr(migration, direction)()

    engine = b.sessions.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: migrate(c, "downgrade"))
        await connection.run_sync(lambda c: migrate(c, "upgrade"))
    read = await b.service.get(b.context.principal, packet.id)
    assert read.rows == old.rows
    new_packet = await b.service.create(b.context.principal, b.payload, "new-client-packet")
    await b.service.generate(
        b.context.principal,
        new_packet.id,
        new_packet.rows[0].id,
        "new-client",
        execution_mode="deep",
    )
    with pytest.raises(RuntimeError, match="presales_execution_policy_history_present"):
        async with engine.begin() as connection:
            await connection.run_sync(lambda c: migrate(c, "downgrade"))
    read = await b.service.get(b.context.principal, new_packet.id)
    assert read.rows[0].attempts[0].execution_policy.mode == "deep"
