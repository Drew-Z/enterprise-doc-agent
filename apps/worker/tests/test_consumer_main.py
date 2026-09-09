import asyncio
import logging
import os
import socket
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import enterprise_doc_worker.consumer_main as consumer_main
from enterprise_doc_core.config import ObservabilitySettings
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.consumer_main import _resolve_process_metrics, consumer_worker_argv
from enterprise_doc_worker.queue import JOB_QUEUE_NAME


def test_consumer_entrypoint_starts_the_registered_job_queue() -> None:
    settings = WorkerSettings(_env_file=None)

    argv = consumer_worker_argv(settings)

    assert argv[:2] == ["worker", "--loglevel"]
    assert argv[argv.index("--pool") + 1] == "solo"
    assert argv[argv.index("--concurrency") + 1] == "1"
    assert argv[argv.index("--queues") + 1] == JOB_QUEUE_NAME
    assert argv[argv.index("--hostname") + 1] == f"{settings.worker.worker_id}@%h"


def test_consumer_reuses_or_binds_the_resource_metrics_registry() -> None:
    existing = MetricsRuntime.create()
    resources = SimpleNamespace(
        multipart_object_store=SimpleNamespace(metrics=existing),
    )
    assert _resolve_process_metrics(resources, None) is existing  # type: ignore[arg-type]

    unbound = SimpleNamespace(
        multipart_object_store=SimpleNamespace(metrics=None),
    )
    provided = MetricsRuntime.create()
    assert _resolve_process_metrics(unbound, provided) is provided  # type: ignore[arg-type]
    assert unbound.multipart_object_store.metrics is provided

    with pytest.raises(ValueError, match="share one metrics registry"):
        _resolve_process_metrics(resources, MetricsRuntime.create())  # type: ignore[arg-type]


@pytest.mark.parametrize("configured_label", ["worker-local", "x" * 200])
def test_consumer_launch_binds_one_fresh_identity_to_hostname_and_claim_factory(
    monkeypatch: pytest.MonkeyPatch,
    configured_label: str,
) -> None:
    settings = WorkerSettings(
        _env_file=None,
        worker={"worker_id": configured_label},
        otel=ObservabilitySettings(metrics_enabled=False),
    )
    compositions: list[dict[str, Any]] = []
    worker_arguments: list[list[str]] = []
    closed: list[str] = []

    class Resources:
        database_engine = object()

        async def close(self) -> None:
            closed.append("resources")

    class Checkpoint:
        async def open(self) -> object:
            return object()

        async def close(self) -> None:
            closed.append("checkpoint")

    class Runner:
        def run(self, awaitable: Any) -> Any:
            return asyncio.run(awaitable)

        def close(self) -> None:
            closed.append("runner")

    def build_app(actual_settings: WorkerSettings, **kwargs: Any) -> Any:
        assert actual_settings is settings
        compositions.append(kwargs)
        app = SimpleNamespace(worker_main=worker_arguments.append)
        return app, kwargs["resources"], kwargs["async_runner"]

    monkeypatch.setattr(consumer_main, "WorkerSettings", lambda: settings)
    monkeypatch.setattr(consumer_main, "configure_logging", lambda **_: None)
    monkeypatch.setattr(consumer_main, "build_foundation_resources", lambda *_, **__: Resources())
    monkeypatch.setattr(consumer_main, "create_session_factory", lambda _: object())
    monkeypatch.setattr(consumer_main, "AsyncTaskRunner", lambda **_: Runner())
    monkeypatch.setattr(consumer_main, "CheckpointRuntime", lambda _: Checkpoint())
    monkeypatch.setattr(consumer_main, "build_durable_agent_handler", lambda **_: object())
    monkeypatch.setattr(consumer_main, "build_consumer_app", build_app)

    consumer_main.main()
    consumer_main.main()

    identities = [composition["worker_id"] for composition in compositions]
    assert identities[0] != identities[1]
    for worker_id, argv in zip(identities, worker_arguments, strict=True):
        assert worker_id.startswith(configured_label[:20])
        assert len(worker_id) <= 200
        assert UUID(hex=worker_id.rsplit("-", 1)[1]).version == 4
        assert argv[argv.index("--hostname") + 1] == f"{worker_id}@%h"
    assert settings.worker.worker_id == configured_label
    assert closed == ["checkpoint", "resources", "runner"] * 2


def test_consumer_composition_records_the_actual_runtime_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = WorkerSettings(_env_file=None)
    with caplog.at_level(logging.INFO, logger="enterprise_doc_worker.consumer_main"):
        app, resources, runner = consumer_main.build_consumer_app(settings)
    try:
        records = [
            record for record in caplog.records if record.msg == "worker_consumer_configured"
        ]
        assert len(records) == 1
        identity = records[0].event_data
        assert identity["process_id"] == os.getpid()
        assert identity["hostname"] == socket.gethostname()
        assert identity["worker_id"].startswith(f"{settings.worker.worker_id}-")
        assert UUID(hex=identity["worker_id"].rsplit("-", 1)[1]).version == 4
        assert set(identity) == {"worker_id", "process_id", "hostname"}
    finally:
        try:
            runner.run(resources.close())
        finally:
            runner.close()
            app.close()


def test_consumer_configuration_logging_failure_does_not_prevent_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError("synthetic-log-sink-unavailable")

    logger = logging.getLogger("enterprise_doc_worker.consumer_main")
    caplog.set_level(logging.INFO, logger=logger.name)
    monkeypatch.setattr(logger, "handlers", [BrokenHandler()])
    monkeypatch.setattr(logger, "propagate", False)
    settings = WorkerSettings(_env_file=None)
    resources = consumer_main.build_foundation_resources(settings)
    runner = consumer_main.AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    app = None
    try:
        app, returned_resources, returned_runner = consumer_main.build_consumer_app(
            settings, resources=resources, async_runner=runner
        )
        assert returned_resources is resources
        assert returned_runner is runner
    finally:
        try:
            runner.run(resources.close())
        finally:
            runner.close()
            if app is not None:
                app.close()
