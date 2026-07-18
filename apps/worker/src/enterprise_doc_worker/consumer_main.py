from __future__ import annotations

import asyncio

from celery import Celery

from enterprise_doc_core.db import create_session_factory, ensure_asyncio_compatibility
from enterprise_doc_core.health import FoundationResources, build_foundation_resources
from enterprise_doc_core.jobs import JobRuntimeService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.handler import build_consumer_factory
from enterprise_doc_worker.queue import (
    JOB_QUEUE_NAME,
    AsyncTaskRunner,
    create_celery_app,
    register_job_task,
)


def build_consumer_app(
    settings: WorkerSettings,
) -> tuple[Celery, FoundationResources, AsyncTaskRunner]:
    """Build a Celery app that has the real document handler registered.

    The probe/publisher process intentionally remains separate. This entrypoint
    is the actual queue consumer and can be scaled independently.
    """
    resources = build_foundation_resources(settings)
    session_factory = create_session_factory(resources.database_engine)
    runtime = JobRuntimeService(session_factory=session_factory)
    app = create_celery_app(settings)
    async_runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    register_job_task(
        app,
        consumer_factory=build_consumer_factory(
            runtime=runtime,
            session_factory=session_factory,
            object_store=resources.multipart_object_store,
            documents_bucket=settings.object_store.documents_bucket,
            worker_id=settings.worker.worker_id,
        ),
        async_runner=async_runner,
    )
    return app, resources, async_runner


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
    app, resources, async_runner = build_consumer_app(settings)
    try:
        app.worker_main(consumer_worker_argv(settings))
    finally:
        try:
            async_runner.run(resources.close())
        finally:
            async_runner.close()


if __name__ == "__main__":
    main()
