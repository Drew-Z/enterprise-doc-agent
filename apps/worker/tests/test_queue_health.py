from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_core.health import ComponentStatus, StaticChecker
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.app import create_probe_app
from enterprise_doc_worker.config import WorkerSettings


async def test_queue_projection_requires_actual_samples_and_does_not_gate_readiness() -> None:
    metrics = MetricsRuntime.create()
    app = create_probe_app(
        metrics=metrics, checkers=[StaticChecker("database", ComponentStatus.UP)]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker") as client:
        unknown = await client.get("/health/queue")
        assert unknown.status_code == 200
        assert unknown.json() == {"healthy": False, "source_at": None}
        metrics.set_queue_oldest_age(0)
        assert (await client.get("/health/queue")).json() == unknown.json()
        sampled = time.time()
        metrics.record_resource_observation("queue", 0, observed_at=sampled)
        metrics.record_resource_observation("redis", 2, observed_at=sampled + 0.001)
        fresh = await client.get("/health/queue")
        assert fresh.json() == {"healthy": True, "source_at": int(sampled * 1000)}
        assert fresh.headers["cache-control"] == "no-store"
        metrics.record_resource_observation("queue", 121, observed_at=time.time())
        assert (await client.get("/health/queue")).json()["healthy"] is False
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"


@pytest.mark.parametrize(
    "scenario", ["failed", "stale", "future", "prestart", "disabled", "stalled"]
)
async def test_queue_projection_rejects_invalid_sources(scenario: str) -> None:
    metrics = MetricsRuntime.create()
    sampled = time.time() + 1
    stamp = sampled - 60 if scenario == "prestart" else sampled
    metrics.record_resource_observation("queue", 0, observed_at=stamp)
    metrics.record_resource_observation("redis", 2, observed_at=stamp)
    if scenario == "failed":
        metrics.record_resource_observation("redis", None, observed_at=sampled + 2)
    now = sampled + 46 if scenario == "stale" else sampled - 16 if scenario == "future" else sampled
    app = create_probe_app(
        settings=WorkerSettings(otel={"metrics_enabled": scenario != "disabled"}),
        metrics=metrics,
        checkers=[],
        clock=lambda: now,
        liveness=lambda: scenario != "stalled",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker") as client:
        response = await client.get("/health/queue")
    assert response.status_code == 200
    assert response.json() == {"healthy": False, "source_at": int(stamp * 1000)}
