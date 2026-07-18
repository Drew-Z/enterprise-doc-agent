from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from celery import Celery
from pydantic import BaseModel, ConfigDict

from enterprise_doc_core.jobs import ClaimedJob, JobRuntimeService, RetryDisposition
from enterprise_doc_worker.config import WorkerSettings

JOB_TASK_NAME = "enterprise_doc_worker.execute_job"
JOB_QUEUE_NAME = "document-ingestion"


class JobMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    tenant_id: UUID
    event_id: UUID


class AsyncJobHandler(Protocol):
    async def __call__(self, claim: ClaimedJob) -> None: ...


class TaskDispatcher(Protocol):
    async def publish(self, message: JobMessage, *, task_id: UUID) -> None: ...


class CeleryTaskDispatcher:
    def __init__(self, app: Celery, *, task_name: str = JOB_TASK_NAME) -> None:
        self.app = app
        self.task_name = task_name

    async def publish(self, message: JobMessage, *, task_id: UUID) -> None:
        await asyncio.to_thread(self._send, message, task_id)

    def _send(self, message: JobMessage, task_id: UUID) -> None:
        self.app.send_task(
            self.task_name,
            args=[message.model_dump(mode="json")],
            task_id=str(task_id),
            queue=JOB_QUEUE_NAME,
        )


class JobDeliveryConsumer:
    def __init__(
        self,
        *,
        runtime: JobRuntimeService,
        worker_id: str,
        handler: AsyncJobHandler,
        classify_error: Callable[[Exception], RetryDisposition] | None = None,
    ) -> None:
        self.runtime = runtime
        self.worker_id = worker_id
        self.handler = handler
        self.classify_error = classify_error or (lambda _: RetryDisposition.RETRYABLE)

    async def handle(self, message: JobMessage) -> str:
        claim = await self.runtime.claim(job_id=message.job_id, worker_id=self.worker_id)
        if claim is None:
            return "duplicate_or_not_claimable"
        try:
            await self.handler(claim)
        except asyncio.CancelledError:
            await self.runtime.fail(
                claim,
                disposition=RetryDisposition.CANCELLED,
                error_code="worker_cancelled",
                error_message="The worker task was cancelled.",
                error_class="CancelledError",
            )
            raise
        except Exception as error:
            await self.runtime.fail(
                claim,
                disposition=self.classify_error(error),
                error_code="job_handler_failed",
                error_message="The job handler failed.",
                error_class=type(error).__name__,
            )
            return "failed"
        await self.runtime.succeed(claim)
        return "succeeded"


def create_celery_app(settings: WorkerSettings | None = None) -> Celery:
    resolved = settings or WorkerSettings()
    app = Celery(
        "enterprise-doc-worker",
        broker=resolved.redis.url.get_secret_value(),
    )
    app.conf.update(
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        result_backend=None,
        task_acks_late=True,
        task_default_queue=JOB_QUEUE_NAME,
        task_ignore_result=True,
        task_reject_on_worker_lost=True,
        task_serializer="json",
    )
    return app


def register_job_task(
    app: Celery,
    *,
    consumer_factory: Callable[[], JobDeliveryConsumer],
) -> None:
    @app.task(  # type: ignore[untyped-decorator]
        bind=True,
        name=JOB_TASK_NAME,
        acks_late=True,
        reject_on_worker_lost=True,
        ignore_result=True,
    )
    def execute_job(_: Any, payload: dict[str, Any]) -> str:
        message = JobMessage.model_validate(payload)
        return asyncio.run(consumer_factory().handle(message))
