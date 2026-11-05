from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from enterprise_doc_core.health import ComponentStatus, StaticChecker
from enterprise_doc_worker.app import create_probe_app
from enterprise_doc_worker.lifecycle import WorkerProgress
from enterprise_doc_worker.publisher import OutboxPublisher


async def test_liveness_rejects_stalled_database_cancellation_without_waiting_for_it(
    caplog,
) -> None:
    now = [100.0]
    progress = WorkerProgress(clock=lambda: now[0])
    pulse = progress.register("publisher", timeout_seconds=10)
    cancelling, release = asyncio.Event(), asyncio.Event()

    class Store:
        async def claim(self, **_):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise

        async def mark_published(self, _):
            raise AssertionError("no work was claimed")

    class Dispatcher:
        async def publish(self, *_, **__):
            raise AssertionError("no work was claimed")

    stop = asyncio.Event()
    publisher = OutboxPublisher(
        store=Store(),
        dispatcher=Dispatcher(),
        publisher_id="test",
        cycle_timeout_seconds=0.01,
        on_progress=pulse,
    )
    task = asyncio.create_task(publisher.run(stop), name="worker.publisher")
    app = create_probe_app(
        checkers=[StaticChecker("database", ComponentStatus.UP), progress],
        liveness=progress.is_healthy,
    )
    try:
        await asyncio.wait_for(cancelling.wait(), 1)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://worker"
        ) as client:
            assert (await client.get("/health/live")).status_code == 200
            now[0] = 110
            response = await asyncio.wait_for(client.get("/health/live"), 0.2)
            assert response.status_code == 503
            assert response.json() == {"status": "stalled"}
            stalled = next(r for r in caplog.records if r.msg == "worker_progress_stalled")
            assert stalled.event_data["role"] == "publisher"
            assert any("publisher.py:run:" in point for point in stalled.event_data["waiting_at"])
            assert not task.done()
            ready = await client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"]["worker_progress"] == {"status": "down"}
            # A late completion cannot revive a process already marked for restart.
            pulse()
            assert (await client.get("/health/live")).status_code == 503
    finally:
        stop.set()
        release.set()
        await asyncio.wait_for(task, 2)


async def test_idle_and_failed_poll_cycles_keep_progress_fresh() -> None:
    now = [100.0]
    progress = WorkerProgress(clock=lambda: now[0])
    pulse = progress.register("publisher", timeout_seconds=10)
    stop = asyncio.Event()
    calls = 0

    class Store:
        async def claim(self, **_):
            nonlocal calls
            calls += 1
            now[0] += 8
            if calls == 1:
                raise OSError("database unavailable")
            assert progress.is_healthy()
            if calls == 4:
                stop.set()
            return ()

    class Dispatcher:
        async def publish(self, *_, **__):
            raise AssertionError("empty queue")

    publisher = OutboxPublisher(
        store=Store(),
        dispatcher=Dispatcher(),
        publisher_id="test",
        poll_interval_seconds=0.001,
        on_progress=pulse,
    )
    await asyncio.wait_for(publisher.run(stop), 1)
    assert calls == 4
    assert now[0] == 132
    assert progress.is_healthy()


async def test_model_budget_is_independent_from_fast_pollers() -> None:
    now = [100.0]
    progress = WorkerProgress(clock=lambda: now[0])
    publisher = progress.register("publisher", timeout_seconds=10)
    presales = progress.register("presales", timeout_seconds=200)
    for second in range(105, 295, 5):
        now[0] = float(second)
        publisher()
        assert await progress.check() == ComponentStatus.UP
    presales()
    now[0] = 299
    publisher()
    assert progress.is_healthy()
    now[0] = 310
    assert await progress.check() == ComponentStatus.DOWN
    publisher()
    presales()
    assert not progress.is_healthy()


async def test_resource_loop_reports_completed_cycles_after_dependency_errors() -> None:
    from enterprise_doc_core.telemetry import MetricsRuntime
    from enterprise_doc_core.telemetry.resources import ResourceMetricsSampler

    now = [100.0]
    progress = WorkerProgress(clock=lambda: now[0])
    stop = asyncio.Event()
    calls = 0

    async def unavailable():
        nonlocal calls
        calls += 1
        now[0] += 8
        if calls == 4:
            stop.set()
        raise OSError("unavailable")

    async def redis():
        return 1.0

    sampler = ResourceMetricsSampler(
        MetricsRuntime.create(),
        unavailable,
        redis,
        interval_seconds=0.001,
        on_progress=progress.register("resource_observer", timeout_seconds=10),
    )
    await asyncio.wait_for(sampler.run(stop), 1)
    assert calls == 4
    assert progress.is_healthy()
