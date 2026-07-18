import asyncio

import uvicorn

from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.jobs import OutboxService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import TelemetryManager
from enterprise_doc_worker.app import create_probe_app
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.lifecycle import WorkerRuntime
from enterprise_doc_worker.publisher import OutboxPublisher
from enterprise_doc_worker.queue import CeleryTaskDispatcher, create_celery_app


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
    business_engine = create_database_engine(settings.database)
    session_factory = create_session_factory(business_engine)
    publisher = OutboxPublisher(
        store=OutboxService(session_factory=session_factory),
        dispatcher=CeleryTaskDispatcher(create_celery_app(settings)),
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
        await business_engine.dispose()
        telemetry.shutdown()


def main() -> None:
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run_worker())


if __name__ == "__main__":
    main()
