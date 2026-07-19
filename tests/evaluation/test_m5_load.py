from __future__ import annotations

import argparse
import asyncio

import httpx
import pytest
import scripts.load_m5 as load_m5
from scripts.load_m5 import RequestSample, build_report, run_workload


def _async_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "scenario": "health",
        "base_url": "http://test",
        "requests": 1,
        "concurrency": 1,
        "token_env": "ENTERPRISE_DOC_LOAD_TOKEN",
        "document_version_id": None,
        "run_id": None,
        "request_timeout_seconds": 1.0,
        "terminal_timeout_seconds": 1.0,
        "poll_seconds": 0.0,
        "target_p95_ms": 250.0,
        "sample_resources": True,
        "resource_process_id": None,
        "resource_sample_interval_seconds": 0.01,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


async def test_health_load_runner_records_percentiles_without_resource_claims() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health/live"
        return httpx.Response(200, json={"status": "alive"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        samples, duration = await run_workload(
            client,
            scenario="health",
            requests=5,
            concurrency=2,
            token=None,
            document_version_id=None,
            run_id=None,
            terminal_timeout_seconds=1,
            poll_seconds=0,
        )

    report = build_report(
        scenario="health",
        requests=5,
        concurrency=2,
        base_url="http://test",
        samples=samples,
        duration_seconds=duration,
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
        target_p95_ms=250,
    )
    assert report.status == "passed"
    assert report.completed_requests == 5
    assert report.resource_saturation["measured"] is False
    assert report.measured["p95_ms"] is not None


def test_load_report_uses_measured_host_saturation_in_bottleneck() -> None:
    resource_saturation = {
        "measured": True,
        "sample_count": 3,
        "host_cpu_percent": {"p95": 92.0},
        "host_memory_percent": {"p95": 61.0},
        "process_rss_bytes": {"max": 64_000_000},
    }
    report = build_report(
        scenario="health",
        requests=1,
        concurrency=1,
        base_url="http://test",
        samples=[RequestSample(duration_ms=5, success=True, status_code=200)],
        duration_seconds=1,
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
        target_p95_ms=250,
        resource_saturation=resource_saturation,
    )

    assert report.resource_saturation["measured"] is True
    assert report.status == "failed"
    assert "CPU" in report.bottleneck
    assert "3 resource samples" in report.capacity_conclusion


async def test_async_main_joins_resource_sampler_when_workload_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler_finished = asyncio.Event()

    async def sampler(**kwargs: object) -> dict[str, object]:
        stop = kwargs["stop"]
        assert isinstance(stop, asyncio.Event)
        await stop.wait()
        sampler_finished.set()
        return {"measured": True, "sample_count": 1}

    async def failing_workload(*args: object, **kwargs: object) -> tuple[object, float]:
        raise RuntimeError("workload failed")

    monkeypatch.setattr(load_m5, "sample_resources", sampler)
    monkeypatch.setattr(load_m5, "run_workload", failing_workload)

    with pytest.raises(RuntimeError, match="workload failed"):
        await load_m5.async_main(_async_args())

    assert sampler_finished.is_set()


async def test_async_main_stops_sampler_when_client_enter_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler_finished = asyncio.Event()

    async def sampler(**kwargs: object) -> dict[str, object]:
        stop = kwargs["stop"]
        assert isinstance(stop, asyncio.Event)
        await stop.wait()
        sampler_finished.set()
        return {"measured": True, "sample_count": 1}

    class FailingClient:
        async def __aenter__(self) -> FailingClient:
            raise RuntimeError("client enter failed")

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    monkeypatch.setattr(load_m5, "sample_resources", sampler)
    monkeypatch.setattr(load_m5.httpx, "AsyncClient", lambda **kwargs: FailingClient())

    with pytest.raises(RuntimeError, match="client enter failed"):
        await load_m5.async_main(_async_args())

    assert sampler_finished.is_set()


async def test_async_main_keeps_workload_result_when_sampler_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_sampler(**kwargs: object) -> dict[str, object]:
        stop = kwargs["stop"]
        assert isinstance(stop, asyncio.Event)
        await stop.wait()
        raise RuntimeError("sampler failed")

    async def successful_workload(
        *args: object, **kwargs: object
    ) -> tuple[list[RequestSample], float]:
        return [RequestSample(duration_ms=5, success=True, status_code=200)], 0.01

    monkeypatch.setattr(load_m5, "sample_resources", failing_sampler)
    monkeypatch.setattr(load_m5, "run_workload", successful_workload)

    report = await load_m5.async_main(_async_args())

    assert report.status == "failed"
    assert report.resource_saturation["measured"] is False
    assert "sampler failed" in str(report.resource_saturation["reason"])


@pytest.mark.parametrize(
    ("duration_ms", "resource_saturation"),
    [
        (251.0, None),
        (
            5.0,
            {
                "measured": True,
                "sample_count": 1,
                "host_cpu_percent": {"p95": 25.0},
                "host_memory_percent": {"p95": 91.0},
            },
        ),
    ],
)
def test_load_report_fails_when_a_declared_target_is_missed(
    duration_ms: float,
    resource_saturation: dict[str, object] | None,
) -> None:
    report = build_report(
        scenario="health",
        requests=1,
        concurrency=1,
        base_url="http://test",
        samples=[RequestSample(duration_ms=duration_ms, success=True, status_code=200)],
        duration_seconds=1,
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
        target_p95_ms=250,
        resource_saturation=resource_saturation,
    )

    assert report.status == "failed"


async def test_async_main_cancellation_joins_resource_sampler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workload_started = asyncio.Event()
    sampler_finished = asyncio.Event()

    async def sampler(**kwargs: object) -> dict[str, object]:
        stop = kwargs["stop"]
        assert isinstance(stop, asyncio.Event)
        await stop.wait()
        sampler_finished.set()
        return {"measured": True, "sample_count": 1}

    async def blocking_workload(*args: object, **kwargs: object) -> tuple[object, float]:
        workload_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(load_m5, "sample_resources", sampler)
    monkeypatch.setattr(load_m5, "run_workload", blocking_workload)

    task = asyncio.create_task(load_m5.async_main(_async_args()))
    await workload_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sampler_finished.is_set()
