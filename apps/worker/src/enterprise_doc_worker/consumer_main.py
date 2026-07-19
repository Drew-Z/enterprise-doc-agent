from __future__ import annotations

import asyncio

from celery import Celery
from prometheus_client import start_http_server

from enterprise_doc_core.agents import CheckpointRuntime
from enterprise_doc_core.db import create_session_factory, ensure_asyncio_compatibility
from enterprise_doc_core.health import FoundationResources, build_foundation_resources
from enterprise_doc_core.jobs import JobRuntimeService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.agent_handler import (
    agent_failure_lock_key,
    project_agent_run_failure,
)
from enterprise_doc_worker.agents import build_durable_agent_handler
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.handler import build_consumer_factory
from enterprise_doc_worker.queue import (
    JOB_QUEUE_NAME,
    AsyncJobHandler,
    AsyncTaskRunner,
    create_celery_app,
    register_job_task,
)


def build_consumer_app(
    settings: WorkerSettings,
    *,
    resources: FoundationResources | None = None,
    async_runner: AsyncTaskRunner | None = None,
    agent_handler: AsyncJobHandler | None = None,
    metrics: MetricsRuntime | None = None,
) -> tuple[Celery, FoundationResources, AsyncTaskRunner]:
    """Build a Celery app that has the real document handler registered.

    The probe/publisher process intentionally remains separate. This entrypoint
    is the actual queue consumer and can be scaled independently.
    """
    resolved_resources = resources or build_foundation_resources(settings)
    session_factory = create_session_factory(resolved_resources.database_engine)
    runtime = JobRuntimeService(
        session_factory=session_factory,
        failure_lock_key=agent_failure_lock_key,
        failure_projector=project_agent_run_failure,
    )
    app = create_celery_app(settings)
    resolved_runner = async_runner or AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    resolved_metrics = metrics if metrics is not None else MetricsRuntime.create()
    register_job_task(
        app,
        consumer_factory=build_consumer_factory(
            runtime=runtime,
            session_factory=session_factory,
            object_store=resolved_resources.multipart_object_store,
            documents_bucket=settings.object_store.documents_bucket,
            worker_id=settings.worker.worker_id,
            agent_handler=agent_handler,
            metrics=resolved_metrics,
            fault_injection=settings.fault_injection,
            embedding_dimension=settings.model.embedding_dimension,
        ),
        async_runner=resolved_runner,
    )
    return app, resolved_resources, resolved_runner


def consumer_worker_argv(settings: WorkerSettings) -> list[str]:
    return [
        "worker",
        "--loglevel",
        settings.log_level,
        "--pool",
        "solo",
        "--concurrency",
        "1",
        "--queues",
        JOB_QUEUE_NAME,
        "--hostname",
        f"{settings.worker.worker_id}@%h",
    ]


def main() -> None:
    ensure_asyncio_compatibility()
    settings = WorkerSettings()
    configure_logging(
        service="worker-consumer",
        environment=settings.app_env.value,
        level=settings.log_level,
    )
    resources = build_foundation_resources(settings)
    session_factory = create_session_factory(resources.database_engine)
    async_runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    checkpoint_runtime = CheckpointRuntime(settings)
    metrics = MetricsRuntime.create()
    metrics_server = None
    metrics_thread = None
    try:
        if settings.otel.metrics_enabled:
            metrics_server, metrics_thread = start_http_server(
                settings.worker.consumer_metrics_port,
                addr=settings.worker.host,
                registry=metrics.registry,
            )
        checkpointer = async_runner.run(checkpoint_runtime.open())
        agent_handler = build_durable_agent_handler(
            session_factory=session_factory,
            model_settings=settings.model,
            mcp_settings=settings.mcp,
            checkpointer=checkpointer,
            graph_version=settings.agent.graph_version,
            fault_injection=settings.fault_injection,
        )
        app, _, _ = build_consumer_app(
            settings,
            resources=resources,
            async_runner=async_runner,
            agent_handler=agent_handler,
            metrics=metrics,
        )
        app.worker_main(consumer_worker_argv(settings))
    finally:
        if metrics_server is not None:
            metrics_server.shutdown()
        if metrics_thread is not None:
            metrics_thread.join(timeout=5)
        try:
            async_runner.run(checkpoint_runtime.close())
            async_runner.run(resources.close())
        finally:
            async_runner.close()


if __name__ == "__main__":
    main()
