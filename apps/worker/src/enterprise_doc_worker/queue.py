from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from time import perf_counter
from typing import Any, Protocol, TypeVar
from uuid import UUID

from celery import Celery
from pydantic import BaseModel, ConfigDict

from enterprise_doc_core.documents.ingestion_service import DocumentIngestionError
from enterprise_doc_core.jobs import (
    ClaimedJob,
    JobLeaseLost,
    JobRuntimeService,
    JobStatus,
    RetryDisposition,
    is_allowed_job_diagnostic_code,
)
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.config import WorkerSettings

JOB_TASK_NAME = "enterprise_doc_worker.execute_job"
JOB_QUEUE_NAME = "document-ingestion"
_ResultT = TypeVar("_ResultT")


class JobMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    tenant_id: UUID
    event_id: UUID


class AsyncJobHandler(Protocol):
    async def __call__(self, claim: ClaimedJob) -> None: ...


class JobHandlerError(Exception):
    code = "job_handler_error"
    message = "The job handler could not complete the task."
    retryable = False
    diagnostic_code: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        diagnostic_code: str | None = None,
    ) -> None:
        self.message = message or type(self).message
        candidate = diagnostic_code or type(self).diagnostic_code
        self.diagnostic_code = candidate if is_allowed_job_diagnostic_code(candidate) else None
        super().__init__(self.message)


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


class AsyncTaskRunner:
    """Run Celery's sync task adapter on one persistent asyncio loop."""

    def __init__(
        self,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    ) -> None:
        self._loop = loop_factory()
        self._started = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._serve,
            name="enterprise-doc-worker-async-loop",
            daemon=True,
        )
        self._thread.start()
        self._started.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def run(self, awaitable: Coroutine[Any, Any, _ResultT]) -> _ResultT:
        if self._closed:
            raise RuntimeError("async task runner is closed")
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()


class JobDeliveryConsumer:
    def __init__(
        self,
        *,
        runtime: JobRuntimeService,
        worker_id: str,
        handler: AsyncJobHandler,
        classify_error: Callable[[Exception], RetryDisposition] | None = None,
        heartbeat_interval_seconds: float | None = None,
        metrics: MetricsRuntime | None = None,
    ) -> None:
        self.runtime = runtime
        self.worker_id = worker_id
        self.handler = handler
        self.classify_error = classify_error or (lambda _: RetryDisposition.RETRYABLE)
        self.metrics = metrics
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else runtime.lease_seconds / 3
        )
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")

    async def handle(self, message: JobMessage) -> str:
        started = perf_counter()
        claim = await self.runtime.claim(job_id=message.job_id, worker_id=self.worker_id)
        if claim is None:
            if self.metrics is not None:
                self.metrics.observe_job_claim(
                    job_type="other",
                    result="duplicate_or_not_claimable",
                )
                self.metrics.observe_job(
                    job_type="other",
                    outcome="duplicate_or_not_claimable",
                    duration=perf_counter() - started,
                )
            return "duplicate_or_not_claimable"

        if self.metrics is not None:
            self.metrics.observe_job_claim(job_type=claim.job_type, result="claimed")

        try:
            outcome = await self._handle_claim(claim)
        except asyncio.CancelledError:
            self._observe_job(claim, outcome="cancelled", started=started)
            raise
        except Exception:
            self._observe_job(claim, outcome="failed", started=started)
            raise
        self._observe_job(claim, outcome=outcome, started=started)
        return outcome

    async def _handle_claim(self, claim: ClaimedJob) -> str:

        handler_task = asyncio.create_task(self.handler(claim))
        heartbeat_task = asyncio.create_task(self._heartbeat_until_cancel_requested(claim))
        try:
            done, _ = await asyncio.wait(
                {handler_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            handler_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)
            await self.runtime.fail(
                claim,
                disposition=RetryDisposition.CANCELLED,
                error_code="worker_cancelled",
                error_message="The worker task was cancelled.",
                error_class="CancelledError",
            )
            raise

        if handler_task in done:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            return await self._settle_handler(claim, handler_task)

        try:
            cancel_requested = heartbeat_task.result()
        except JobLeaseLost:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            raise
        except Exception as error:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            await self.runtime.fail(
                claim,
                disposition=RetryDisposition.RETRYABLE,
                error_code="job_heartbeat_failed",
                error_message="The worker could not renew the job lease.",
                error_class=type(error).__name__,
            )
            return "failed"

        if not cancel_requested:
            raise RuntimeError("heartbeat monitor stopped without a cancellation request")
        handler_task.cancel()
        await asyncio.gather(handler_task, return_exceptions=True)
        await self.runtime.fail(
            claim,
            disposition=RetryDisposition.CANCELLED,
            error_code="job_cancel_requested",
            error_message="The job was cancelled by request.",
            error_class="CancelledError",
        )
        return "cancelled"

    async def _heartbeat_until_cancel_requested(self, claim: ClaimedJob) -> bool:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            try:
                cancel_requested = await self.runtime.heartbeat(claim)
            except JobLeaseLost:
                if self.metrics is not None:
                    self.metrics.observe_heartbeat("lease_lost")
                raise
            except Exception:
                if self.metrics is not None:
                    self.metrics.observe_heartbeat("error")
                raise
            if cancel_requested:
                if self.metrics is not None:
                    self.metrics.observe_heartbeat("cancel_requested")
                return True
            if self.metrics is not None:
                self.metrics.observe_heartbeat("ok")

    def _observe_job(self, claim: ClaimedJob, *, outcome: str, started: float) -> None:
        if self.metrics is not None:
            self.metrics.observe_job(
                job_type=claim.job_type,
                outcome=outcome,
                duration=perf_counter() - started,
            )

    async def _settle_handler(
        self,
        claim: ClaimedJob,
        handler_task: asyncio.Task[None],
    ) -> str:
        try:
            await handler_task
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
            diagnostic_code: str | None = None
            if isinstance(error, DocumentIngestionError):
                error_code = error.code
                error_message = error.message
            elif isinstance(error, JobHandlerError):
                error_code = error.code
                error_message = type(error).message
                diagnostic_code = error.diagnostic_code
            else:
                error_code = "job_handler_failed"
                error_message = "The job handler failed."
            failure = await self.runtime.fail(
                claim,
                disposition=self.classify_error(error),
                error_code=error_code,
                error_message=error_message,
                error_class=type(error).__name__,
                diagnostic_code=diagnostic_code,
            )
            return "cancelled" if failure.status == JobStatus.CANCELLED.value else "failed"
        final_status = await self.runtime.succeed(claim)
        return "cancelled" if final_status == JobStatus.CANCELLED.value else "succeeded"


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
    async_runner: AsyncTaskRunner | None = None,
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
        awaitable = consumer_factory().handle(message)
        if async_runner is not None:
            return async_runner.run(awaitable)
        return asyncio.run(awaitable)
