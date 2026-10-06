from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import event, select

from enterprise_doc_core.billing.models import UsageEvent, UsageReservation
from enterprise_doc_core.demo.models import DemoWorkspace
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall
from enterprise_doc_worker.lifecycle import WorkerProgress
from enterprise_doc_worker.presales import run_presales_pool
from tests.presales.fixtures import add_chunk
from tests.presales.test_presales_background_integration import (
    background as background,
)
from tests.presales.test_presales_background_integration import (
    browser_db as browser_db,
)
from tests.presales.test_presales_background_integration import (
    configured_worker,
    enqueue,
    valid_response,
)

pytestmark = pytest.mark.integration


async def wait_for_terminal(b, count):
    async with asyncio.timeout(10):
        while True:
            async with b.sessions() as session:
                states = (await session.scalars(select(PresalesAttempt.state))).all()
            if len(states) == count and all(state == "succeeded" for state in states):
                return
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_slots_overlap_model_wait_and_leave_next_queued_without_duplicate_charge(
    background, concurrency
):
    b = background
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def respond(request):
        requests.append(request)
        if len(requests) == concurrency:
            entered.set()
        await release.wait()
        return valid_response(request)

    worker = configured_worker(b, respond)
    count = concurrency + 1
    for index in range(count):
        await enqueue(b, f"pool-{index}")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(worker, shutdown, worker_id="pool-test", concurrency=concurrency)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with b.sessions() as session:
            assert (
                sorted((await session.scalars(select(PresalesAttempt.state))).all())
                == ["queued"] + ["running"] * concurrency
            )
            assert (
                sorted((await session.scalars(select(Job.attempts))).all())
                == [0] + [1] * concurrency
            )
        assert len(requests) == concurrency
        release.set()
        await wait_for_terminal(b, count)
    finally:
        shutdown.set()
        release.set()
        await asyncio.wait_for(task, 5)
    assert len(requests) == count
    async with b.sessions() as session:
        assert (await session.scalars(select(UsageReservation.state))).all() == ["consumed"] * count
        consumes = (
            await session.scalars(select(UsageEvent).where(UsageEvent.event_type == "consume"))
        ).all()
        assert len(consumes) == count
        assert len({event.operation_id for event in consumes}) == count
        calls = (await session.scalars(select(PresalesProviderCall))).all()
        assert len(calls) == count and all(call.state == "succeeded" for call in calls)
        assert (await session.scalars(select(Job.attempts))).all() == [1] * count


async def test_demo_remains_serial_while_regular_tenant_can_finish(background):
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
    await add_chunk(
        b.sessions,
        b.other,
        b.other.document_version_id,
        b.other.generation_id,
        "Retention is 30 days.",
    )
    demo_started, normal_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    demo_requests = []

    async def respond(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        if payload["requirement"]["key"] == "R1":
            demo_requests.append(request)
            demo_started.set()
            await release.wait()
        else:
            normal_started.set()
        return valid_response(request)

    worker = configured_worker(b, respond)
    first = await enqueue(b, "demo-first")
    second = await enqueue(b, "demo-second")
    payload = b.payload.model_copy(deep=True)
    payload.requirements[0].key = "normal"
    payload.sources[0].version_id = b.other.document_version_id
    normal = await b.service.create(b.other.principal, payload, "normal-create")
    await b.service.generate(b.other.principal, normal.id, normal.rows[0].id, "normal-once")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(worker, shutdown, worker_id="demo-pool", concurrency=2)
    )
    try:
        await asyncio.wait_for(demo_started.wait(), 5)
        await asyncio.wait_for(normal_started.wait(), 5)
        second_state = await b.service.get(b.context.principal, second.id)
        assert second_state.rows[0].state == "queued"
        assert len(demo_requests) == 1
        release.set()
        await wait_for_terminal(b, 3)
    finally:
        release.set()
        shutdown.set()
        await asyncio.wait_for(task, 5)
    assert len(demo_requests) == 2
    assert (await b.service.get(b.context.principal, first.id)).rows[0].state == "drafted"
    async with b.sessions() as session:
        demo = await session.scalar(select(DemoWorkspace))
        assert demo.attempts_used == 2 and demo.active_attempt_id is None


@pytest.mark.parametrize("stop", ["shutdown", "cancel"])
async def test_stopping_cancels_all_active_http_work_and_preserves_dispatch_leases(
    background, stop
):
    b = background
    entered, never = asyncio.Event(), asyncio.Event()
    started, cancelled = [], []

    async def respond(request):
        started.append(request)
        if len(started) == 2:
            entered.set()
        try:
            await never.wait()
            return valid_response(request)
        finally:
            cancelled.append(request)

    worker = configured_worker(b, respond)
    for index in range(3):
        await enqueue(b, f"cancel-{index}")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(worker, shutdown, worker_id="cancel-pool", concurrency=2)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if stop == "shutdown":
            shutdown.set()
            await asyncio.wait_for(task, 5)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(started) == len(cancelled) == 2
    assert not any(t.get_name().startswith("worker.presales.") for t in asyncio.all_tasks())
    async with b.sessions() as session:
        assert sorted((await session.scalars(select(Job.status))).all()) == [
            "pending",
            "running",
            "running",
        ]
        assert (await session.scalars(select(UsageReservation.state))).all() == ["reserved"] * 3
        assert (await session.scalars(select(PresalesProviderCall.state))).all() == ["running"] * 2
        assert not (
            await session.scalars(select(UsageEvent).where(UsageEvent.event_type == "consume"))
        ).all()


async def test_idle_slot_cannot_hide_another_slots_stalled_model(background):
    b = background
    now = [0.0]
    progress = WorkerProgress(clock=lambda: now[0])
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        await release.wait()
        return valid_response(request)

    worker = configured_worker(b, respond)
    await enqueue(b, "stalled")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(
            worker,
            shutdown,
            worker_id="health-pool",
            concurrency=2,
            progress=progress,
            progress_timeout_seconds=60,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        now[0] = 59
        await asyncio.sleep(1.1)  # Let the empty lane report its own completed poll.
        assert progress.is_healthy()
        now[0] = 61
        assert not progress.is_healthy()
        release.set()
        await wait_for_terminal(b, 1)
        assert not progress.is_healthy()  # A late completion cannot revive the process.
    finally:
        release.set()
        shutdown.set()
        await asyncio.wait_for(task, 5)


async def test_database_poll_failure_cancels_other_slots_before_pool_exits(background):
    b = background
    entered, cancelled, never = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        try:
            await never.wait()
            return valid_response(request)
        finally:
            cancelled.set()

    worker = configured_worker(b, respond)
    await enqueue(b, "db-failure")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(worker, shutdown, worker_id="fault-pool", concurrency=2)
    )
    engine = b.sessions.kw["bind"].sync_engine

    def unavailable(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT jobs.id"):
            raise OSError("controlled database connection failure")

    try:
        await asyncio.wait_for(entered.wait(), 5)
        event.listen(engine, "before_cursor_execute", unavailable)
        with pytest.raises(OSError, match="controlled database connection failure"):
            await asyncio.wait_for(task, 5)
        assert cancelled.is_set()
        assert not any(t.get_name().startswith("worker.presales.") for t in asyncio.all_tasks())
    finally:
        if event.contains(engine, "before_cursor_execute", unavailable):
            event.remove(engine, "before_cursor_execute", unavailable)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("daily_limit", [1, 200])
async def test_parallel_failures_cannot_exceed_shared_daily_or_per_operation_dispatch_limits(
    background, daily_limit
):
    b = background
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(503)

    worker = configured_worker(b, respond, daily_dispatch_limit=daily_limit)
    for index in range(2):
        await enqueue(b, f"budget-{index}")
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_presales_pool(worker, shutdown, worker_id="budget-pool", concurrency=2)
    )
    try:
        async with asyncio.timeout(10):
            while True:
                async with b.sessions() as session:
                    states = (await session.scalars(select(PresalesAttempt.state))).all()
                if states == ["failed", "failed"]:
                    break
                await asyncio.sleep(0.02)
    finally:
        shutdown.set()
        await asyncio.wait_for(task, 5)
    assert len(requests) == (1 if daily_limit == 1 else 4)
    async with b.sessions() as session:
        calls = (await session.scalars(select(PresalesProviderCall))).all()
        assert len(calls) == len(requests)
        assert all(sum(c.operation_id == call.operation_id for c in calls) <= 2 for call in calls)
        assert (await session.scalars(select(UsageReservation.state))).all() == ["released"] * 2
