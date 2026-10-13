from __future__ import annotations

import asyncio
import math

import pytest

from enterprise_doc_core.telemetry import MetricsRuntime


def test_resource_metrics_start_unknown_without_a_successful_observation():
    metrics = MetricsRuntime.create()
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds"))
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_redis_connections"))
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients"))
    for source in ("queue", "redis"):
        assert (
            metrics.registry.get_sample_value(
                "enterprise_doc_resource_sample_success", {"source": source}
            )
            == 0
        )
        assert (
            metrics.registry.get_sample_value(
                "enterprise_doc_resource_last_success_timestamp_seconds", {"source": source}
            )
            == 0
        )


async def test_resource_sampling_keeps_sources_independent_and_retains_last_success():
    from enterprise_doc_core.telemetry.resources import ResourceMetricsSampler

    metrics = MetricsRuntime.create()
    now = [100.0]
    unavailable = [False]

    async def queue():
        if unavailable[0]:
            raise OSError("private database endpoint")
        return 3.5

    async def redis():
        return 8.0

    sampler = ResourceMetricsSampler(metrics, queue, redis, clock=lambda: now[0])
    await sampler.sample_once()
    assert metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds") == 3.5
    assert metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients") == 8
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_sample_success", {"source": "queue"}
        )
        == 1
    )
    now[0] = 110
    unavailable[0] = True
    await sampler.sample_once()
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds"))
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_sample_success", {"source": "queue"}
        )
        == 0
    )
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_last_success_timestamp_seconds", {"source": "queue"}
        )
        == 100
    )
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_last_success_timestamp_seconds", {"source": "redis"}
        )
        == 110
    )
    assert "private database endpoint" not in metrics.render().decode()
    unavailable[0] = False
    now[0] = 120
    await sampler.sample_once()
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_last_success_timestamp_seconds", {"source": "queue"}
        )
        == 120
    )


async def test_resource_sampling_timeout_cancels_only_its_source_and_allows_shutdown():
    from enterprise_doc_core.telemetry.resources import ResourceMetricsSampler

    metrics = MetricsRuntime.create()
    cancelled = asyncio.Event()

    async def hung():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def redis():
        return 4.0

    sampler = ResourceMetricsSampler(metrics, hung, redis, timeout_seconds=0.01)
    await asyncio.wait_for(sampler.sample_once(), timeout=1)
    assert cancelled.is_set()
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds"))
    assert metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients") == 4
    stopped = asyncio.Event()
    stopped.set()
    await asyncio.wait_for(sampler.run(stopped), timeout=1)


async def test_resource_sampling_cancellation_drains_readers():
    from enterprise_doc_core.telemetry.resources import ResourceMetricsSampler

    started = asyncio.Event()
    active = [0]

    async def hung():
        active[0] += 1
        if active[0] == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active[0] -= 1

    sampler = ResourceMetricsSampler(MetricsRuntime.create(), hung, hung)
    task = asyncio.create_task(sampler.run(asyncio.Event()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active[0] == 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, 1.5])
def test_invalid_redis_readings_cannot_refresh_success(value):
    metrics = MetricsRuntime.create()
    metrics.record_resource_observation("redis", 2, observed_at=100)
    metrics.record_resource_observation("redis", value, observed_at=110)
    assert math.isnan(metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients"))
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_last_success_timestamp_seconds", {"source": "redis"}
        )
        == 100
    )
    assert (
        metrics.registry.get_sample_value(
            "enterprise_doc_resource_sample_success", {"source": "redis"}
        )
        == 0
    )


@pytest.mark.parametrize(
    "info", [{}, {"connected_clients": True}, {"connected_clients": "5"}, {"connected_clients": -1}]
)
async def test_redis_info_reader_rejects_missing_or_ambiguous_counts(info):
    from enterprise_doc_core.telemetry.resources import read_redis_connected_clients

    class Client:
        async def info(self, section):
            assert section == "clients"
            return info

    with pytest.raises(ValueError, match="invalid_redis_connected_clients"):
        await read_redis_connected_clients(Client())
