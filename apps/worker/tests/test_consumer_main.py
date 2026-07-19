from types import SimpleNamespace

import pytest

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
