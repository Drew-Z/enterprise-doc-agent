from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import enterprise_doc_worker.__main__ as worker_main
from enterprise_doc_core.telemetry import MetricsRuntime


def test_worker_main_passes_process_metrics_to_agent_handler() -> None:
    source = Path(worker_main.__file__).read_text(encoding="utf-8")
    handler_call = source[source.index("build_durable_agent_handler(") :]
    handler_call = handler_call[: handler_call.index(")")]
    assert "metrics=metrics" in handler_call


class FakeTelemetry:
    tracer = object()

    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeTelemetryManager:
    def __init__(self, telemetry: FakeTelemetry) -> None:
        self.telemetry = telemetry

    def initialize(self, **_: object) -> FakeTelemetry:
        return self.telemetry


class FakeRuntime:
    def __init__(self, **_: object) -> None:
        self.shutdown_requested = False

    def request_shutdown(self) -> None:
        self.shutdown_requested = True


class FakeResources:
    database_engine = object()
    multipart_object_store = object()
    checkers = ()

    def __init__(self) -> None:
        self.close_called = False

    async def close(self) -> None:
        self.close_called = True


class FailingCheckpointRuntime:
    def __init__(self, _: object) -> None:
        self.close_called = False

    async def open(self) -> object:
        raise RuntimeError("checkpoint unavailable")

    async def close(self) -> bool:
        self.close_called = True
        return False


@pytest.mark.asyncio
async def test_run_worker_cleans_resources_when_checkpoint_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = FakeTelemetry()
    resources = FakeResources()
    runtime = FakeRuntime()
    checkpoint = FailingCheckpointRuntime(object())
    foundation_kwargs: dict[str, object] = {}
    settings = SimpleNamespace(
        app_env=SimpleNamespace(value="test"),
        log_level="INFO",
        otel=object(),
    )

    monkeypatch.setattr(worker_main, "WorkerSettings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda **_: None)
    monkeypatch.setattr(
        worker_main,
        "TelemetryManager",
        lambda: FakeTelemetryManager(telemetry),
    )
    monkeypatch.setattr(worker_main, "WorkerRuntime", lambda **_: runtime)

    def build_resources(_: object, **kwargs: object) -> FakeResources:
        foundation_kwargs.update(kwargs)
        return resources

    monkeypatch.setattr(worker_main, "build_foundation_resources", build_resources)
    monkeypatch.setattr(worker_main, "create_session_factory", lambda _: object())
    monkeypatch.setattr(worker_main, "JobRuntimeService", lambda **_: object())
    monkeypatch.setattr(worker_main, "create_celery_app", lambda _: object())
    monkeypatch.setattr(worker_main, "CheckpointRuntime", lambda _: checkpoint)

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await worker_main.run_worker()

    assert runtime.shutdown_requested is True
    assert checkpoint.close_called is True
    assert resources.close_called is True
    assert telemetry.shutdown_called is True
    assert isinstance(foundation_kwargs["metrics"], MetricsRuntime)
