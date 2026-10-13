from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.queue_probe import WORKER_QUEUE_URL, QueueProbe
from enterprise_doc_core.health import ComponentStatus, StaticChecker
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.app import create_probe_app


async def test_api_forwards_real_worker_projection_without_gating_dependency_readiness() -> None:
    metrics = MetricsRuntime.create()
    sampled = time.time()
    metrics.record_resource_observation("queue", 0, observed_at=sampled)
    metrics.record_resource_observation("redis", 2, observed_at=sampled)
    worker = create_probe_app(metrics=metrics, checkers=[])
    api = create_app(
        settings=ApiSettings(api={"queue_observation_enabled": True}),
        checkers=[StaticChecker("database", ComponentStatus.UP)],
        queue_probe_transport=ASGITransport(app=worker),
    )
    async with api.router.lifespan_context(api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api") as client:
            response = await client.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["queue"] == {"healthy": True, "source_at": int(sampled * 1000)}
            assert response.headers["cache-control"] == "no-store"


async def test_missing_worker_does_not_remove_healthy_api_from_service() -> None:
    # A fresh Worker without successful sampling is unknown, even when its own
    # dependency readiness happens to be up.
    worker = create_probe_app(checkers=[StaticChecker("database", ComponentStatus.UP)])
    api = create_app(
        settings=ApiSettings(api={"queue_observation_enabled": True}),
        checkers=[StaticChecker("database", ComponentStatus.UP)],
        queue_probe_transport=ASGITransport(app=worker),
    )
    async with api.router.lifespan_context(api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api") as client:
            response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["queue"] == {"healthy": False, "source_at": None}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"healthy": True},
        {"healthy": "true", "source_at": 1},
        {"healthy": True, "source_at": True},
        {"healthy": True, "source_at": -1},
        {"healthy": True, "source_at": 1.5},
        {"healthy": True, "source_at": 2**53},
        {"healthy": True, "source_at": 1, "private": "PRIVATE_BODY"},
    ],
)
async def test_invalid_projection_never_reports_success(body: dict) -> None:
    async def network(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    probe = QueueProbe(transport=httpx.MockTransport(network))
    try:
        await probe.sample_once()
        assert probe.current.healthy is False
        assert "PRIVATE_BODY" not in probe.current.model_dump_json()
    finally:
        await probe.close()


@pytest.mark.parametrize(
    "failure",
    ["redirect", "status", "content_type", "oversize", "json", "transport", "stale", "future"],
)
async def test_failed_poll_replaces_success_without_retry_or_forwarding_body(failure: str) -> None:
    now = time.time()
    calls = []

    async def network(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert str(request.url) == WORKER_QUEUE_URL
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if len(calls) == 1:
            return httpx.Response(200, json={"healthy": True, "source_at": int(now * 1000)})
        if failure == "transport":
            raise httpx.ConnectError("PRIVATE_BODY")
        if failure in {"stale", "future"}:
            return httpx.Response(
                200,
                json={
                    "healthy": True,
                    "source_at": int(now * 1000) + (-46_000 if failure == "stale" else 16_000),
                },
            )
        return httpx.Response(
            302 if failure == "redirect" else 503 if failure == "status" else 200,
            headers={
                "content-type": "text/html" if failure == "content_type" else "application/json",
                "location": "http://other.test/private",
            },
            content=b"x" * 1025 if failure == "oversize" else b"PRIVATE_BODY",
        )

    probe = QueueProbe(transport=httpx.MockTransport(network), clock=lambda: now)
    try:
        await probe.sample_once()
        assert probe.current.healthy is True
        await probe.sample_once()
        assert probe.current.healthy is False
        assert len(calls) == 2
        assert "PRIVATE_BODY" not in probe.current.model_dump_json()
    finally:
        await probe.close()


async def test_stalled_body_is_closed_at_total_deadline_and_poll_recovers() -> None:
    closed = asyncio.Event()
    calls = 0

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"healthy":'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    async def network(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, headers={"content-type": "application/json"}, stream=Stream()
            )
        return httpx.Response(200, json={"healthy": True, "source_at": int(time.time() * 1000)})

    probe = QueueProbe(transport=httpx.MockTransport(network), timeout_seconds=0.05)
    try:
        await asyncio.wait_for(probe.sample_once(), timeout=1)
        assert closed.is_set()
        assert probe.current.healthy is False
        await probe.sample_once()
        assert probe.current.healthy is True
    finally:
        await probe.close()


async def test_stopped_poller_cannot_refresh_source_time_and_shutdown_closes_transport() -> None:
    now = time.time()
    stamp = int(now * 1000)
    calls = 0
    sampled = asyncio.Event()
    stop = asyncio.Event()
    closed = False

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal calls
            calls += 1
            sampled.set()
            stop.set()
            return httpx.Response(200, json={"healthy": True, "source_at": stamp})

        async def aclose(self):
            nonlocal closed
            closed = True

    probe = QueueProbe(transport=Transport(), clock=lambda: now, interval_seconds=0.01)
    task = asyncio.create_task(probe.run(stop))
    try:
        await asyncio.wait_for(sampled.wait(), timeout=1)
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        assert probe.current.healthy is True
        now += 46
        assert probe.current.model_dump() == {"healthy": False, "source_at": stamp}
        assert calls == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await probe.close()
    assert closed
