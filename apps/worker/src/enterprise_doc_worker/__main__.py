import asyncio

import uvicorn

from enterprise_doc_core.db import (
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.health import build_foundation_resources
from enterprise_doc_core.jobs import JobRuntimeService, OutboxService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import TelemetryManager
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
    job_runtime = JobRuntimeService(session_factory=session_factory)
    celery_app = create_celery_app(settings)

    register_job_task(
        celery_app,
        consumer_factory=build_consumer_factory(
            runtime=job_runtime,
            session_factory=session_factory,
            object_store=resources.multipart_object_store,
            documents_bucket=settings.object_store.documents_bucket,
            worker_id=settings.worker.worker_id,
        ),
    )
    publisher = OutboxPublisher(
        store=OutboxService(session_factory=session_factory),
        dispatcher=CeleryTaskDispatcher(celery_app),
        publisher_id=settings.worker.worker_id,
        batch_size=settings.worker.publisher_batch_size,
        poll_interval_seconds=settings.worker.publisher_poll_interval_seconds,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_probe_app(settings=settings),
            host=settings.worker.host,
            port=settings.worker.probe_port,
            loop="none",
            log_config=None,
        )
    )
    runtime_task = asyncio.create_task(runtime.run())
    publisher_task = asyncio.create_task(publisher.run(runtime.shutdown_event))
    try:
        await server.serve()
    finally:
        runtime.request_shutdown()
        await runtime_task
        publisher_task.cancel()
        await asyncio.gather(publisher_task, return_exceptions=True)
        await resources.close()
        telemetry.shutdown()


def main() -> None:
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run_worker())


if __name__ == "__main__":
    main()
