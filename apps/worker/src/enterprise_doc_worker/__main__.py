import asyncio
from collections.abc import Coroutine
from typing import Any

import uvicorn

from enterprise_doc_core.agents import CheckpointHealthChecker, CheckpointRuntime
from enterprise_doc_core.db import (
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.health import FoundationResources, build_foundation_resources
from enterprise_doc_core.jobs import JobRuntimeService, OutboxService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import MetricsRuntime, TelemetryManager, TelemetryRuntime
from enterprise_doc_worker.agent_handler import (
    agent_failure_lock_key,
    project_agent_run_failure,
)
from enterprise_doc_worker.agents import build_durable_agent_handler
from enterprise_doc_worker.app import create_probe_app
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.handler import build_consumer_factory
from enterprise_doc_worker.lifecycle import WorkerRuntime
from enterprise_doc_worker.publisher import OutboxPublisher
from enterprise_doc_worker.queue import (
    CeleryTaskDispatcher,
    create_celery_app,
    register_job_task,
)


async def supervise_worker_tasks(
    *,
    server: Coroutine[Any, Any, None],
    runtime: Coroutine[Any, Any, None],
    publisher: Coroutine[Any, Any, None],
) -> None:
    tasks = {
        "server": asyncio.create_task(server),
        "runtime": asyncio.create_task(runtime),
        "publisher": asyncio.create_task(publisher),
    }
    try:
        done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
        for role in ("runtime", "publisher"):
            task = tasks[role]
            if task not in done:
                continue
            if task.cancelled():
                raise RuntimeError(f"{role} was cancelled unexpectedly")
            error = task.exception()
            if error is not None:
                raise RuntimeError(f"{role} failed") from error
            raise RuntimeError(f"{role} stopped unexpectedly")
        await tasks["server"]
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def run_worker() -> None:
    settings = WorkerSettings()
    telemetry: TelemetryRuntime | None = None
    runtime: WorkerRuntime | None = None
    resources: FoundationResources | None = None
    checkpoint_runtime: CheckpointRuntime | None = None
    metrics = MetricsRuntime.create()
    try:
        configure_logging(
            service="worker",
            environment=settings.app_env.value,
            level=settings.log_level,
        )
        telemetry = TelemetryManager().initialize(
            settings=settings.otel,
            service_name="enterprise-doc-worker",
        )
        runtime = WorkerRuntime(tracer=telemetry.tracer)
        resources = build_foundation_resources(settings, metrics=metrics)
        session_factory = create_session_factory(resources.database_engine)
        job_runtime = JobRuntimeService(
            session_factory=session_factory,
            failure_lock_key=agent_failure_lock_key,
            failure_projector=project_agent_run_failure,
        )
        celery_app = create_celery_app(settings)
        checkpoint_runtime = CheckpointRuntime(settings)
        checkpointer = await checkpoint_runtime.open()
        agent_handler = build_durable_agent_handler(
            session_factory=session_factory,
            model_settings=settings.model,
            mcp_settings=settings.mcp,
            checkpointer=checkpointer,
            graph_version=settings.agent.graph_version,
            fault_injection=settings.fault_injection,
            metrics=metrics,
        )

        register_job_task(
            celery_app,
            consumer_factory=build_consumer_factory(
                runtime=job_runtime,
                session_factory=session_factory,
                object_store=resources.multipart_object_store,
                documents_bucket=settings.object_store.documents_bucket,
                worker_id=settings.worker.worker_id,
                agent_handler=agent_handler,
                metrics=metrics,
                fault_injection=settings.fault_injection,
                embedding_dimension=settings.model.embedding_dimension,
            ),
        )
        publisher = OutboxPublisher(
            store=OutboxService(session_factory=session_factory),
            dispatcher=CeleryTaskDispatcher(celery_app),
            publisher_id=settings.worker.worker_id,
            batch_size=settings.worker.publisher_batch_size,
            poll_interval_seconds=settings.worker.publisher_poll_interval_seconds,
            cycle_timeout_seconds=settings.worker.publisher_cycle_timeout_seconds,
            metrics=metrics,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_probe_app(
                    settings=settings,
                    checkers=(*resources.checkers, CheckpointHealthChecker(settings)),
                    metrics=metrics,
                ),
                host=settings.worker.host,
                port=settings.worker.probe_port,
                loop="none",
                log_config=None,
            )
        )
        await supervise_worker_tasks(
            server=server.serve(),
            runtime=runtime.run(),
            publisher=publisher.run(runtime.shutdown_event),
        )
    finally:
        if runtime is not None:
            runtime.request_shutdown()
        if checkpoint_runtime is not None:
            await checkpoint_runtime.close()
        if resources is not None:
            await resources.close()
        if telemetry is not None:
            telemetry.shutdown()


def main() -> None:
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run_worker())


if __name__ == "__main__":
    main()
