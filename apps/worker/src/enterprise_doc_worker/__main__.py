import asyncio

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


async def run_worker() -> None:
    settings = WorkerSettings()
    telemetry: TelemetryRuntime | None = None
    runtime: WorkerRuntime | None = None
    resources: FoundationResources | None = None
    checkpoint_runtime: CheckpointRuntime | None = None
    runtime_task: asyncio.Task[None] | None = None
    publisher_task: asyncio.Task[None] | None = None
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
        resources = build_foundation_resources(settings)
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
        runtime_task = asyncio.create_task(runtime.run())
        publisher_task = asyncio.create_task(publisher.run(runtime.shutdown_event))
        await server.serve()
    finally:
        if runtime is not None:
            runtime.request_shutdown()
        if publisher_task is not None and not publisher_task.done():
            publisher_task.cancel()
        tasks = tuple(task for task in (runtime_task, publisher_task) if task is not None)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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
