from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.documents.ingestion_service import DocumentIngestionError
from enterprise_doc_core.jobs import ClaimedJob, JobFailureResult, RetryDisposition
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.queue import (
    JOB_QUEUE_NAME,
    JOB_TASK_NAME,
    AsyncTaskRunner,
    CeleryTaskDispatcher,
    JobDeliveryConsumer,
    JobMessage,
    create_celery_app,
    register_job_task,
)


@dataclass
class FakeCelery:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})


class FakeRuntime:
    lease_seconds = 30.0

    def __init__(
        self,
        claim: ClaimedJob | None,
        *,
        heartbeat_results: list[bool | Exception] | None = None,
        succeed_status: str = "succeeded",
    ) -> None:
        self.claim_result = claim
        self.succeeded: list[ClaimedJob] = []
        self.failed: list[tuple[ClaimedJob, RetryDisposition]] = []
        self.failure_metadata: list[dict[str, Any]] = []
        self.heartbeats: list[ClaimedJob] = []
        self.heartbeat_results = heartbeat_results or []
        self.heartbeat_event: asyncio.Event | None = None
        self.succeed_status = succeed_status

    async def claim(self, *, job_id, worker_id):
        return self.claim_result

    async def heartbeat(self, claim: ClaimedJob) -> bool:
        self.heartbeats.append(claim)
        if self.heartbeat_event is not None:
            self.heartbeat_event.set()
        if not self.heartbeat_results:
            return False
        result = self.heartbeat_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def succeed(self, claim: ClaimedJob) -> str:
        self.succeeded.append(claim)
        return self.succeed_status

    async def fail(
        self,
        claim: ClaimedJob,
        *,
        disposition: RetryDisposition,
        **metadata: Any,
    ) -> JobFailureResult:
        self.failed.append((claim, disposition))
        self.failure_metadata.append(metadata)
        return JobFailureResult("retry_wait", None, claim.attempt_id)


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=uuid4(),
        actor_id=uuid4(),
        worker_id="worker-a",
        lease_token=uuid4(),
        fencing_token=1,
        job_type="document.ingest",
        payload={},
    )


def test_job_message_contains_only_stable_identifiers() -> None:
    message = JobMessage(job_id=uuid4(), tenant_id=uuid4(), event_id=uuid4())

    assert set(message.model_dump()) == {"job_id", "tenant_id", "event_id"}
    with pytest.raises(ValidationError):
        JobMessage.model_validate({**message.model_dump(), "document_text": "secret"})


async def test_celery_dispatcher_uses_stable_event_id_as_task_id() -> None:
    fake = FakeCelery()
    dispatcher = CeleryTaskDispatcher(fake)  # type: ignore[arg-type]
    message = JobMessage(job_id=uuid4(), tenant_id=uuid4(), event_id=uuid4())

    await dispatcher.publish(message, task_id=message.event_id)

    assert fake.calls == [
        {
            "name": JOB_TASK_NAME,
            "args": [message.model_dump(mode="json")],
            "task_id": str(message.event_id),
            "queue": JOB_QUEUE_NAME,
        }
    ]


async def test_duplicate_delivery_does_not_call_handler() -> None:
    runtime = FakeRuntime(None)
    handler_calls = 0

    async def handler(_: ClaimedJob) -> None:
        nonlocal handler_calls
        handler_calls += 1

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
    )
    message = JobMessage(job_id=uuid4(), tenant_id=uuid4(), event_id=uuid4())

    assert await consumer.handle(message) == "duplicate_or_not_claimable"
    assert handler_calls == 0


async def test_consumer_records_bounded_job_metrics() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim)
    metrics = MetricsRuntime.create()

    async def handler(_: ClaimedJob) -> None:
        return None

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
        metrics=metrics,
    )

    assert (
        await consumer.handle(
            JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())
        )
        == "succeeded"
    )
    rendered = metrics.render().decode("utf-8")
    assert 'job_type="document.ingest"' in rendered
    assert 'outcome="succeeded"' in rendered
    assert str(claim.job_id) not in rendered


async def test_consumer_heartbeats_until_handler_completes() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim)
    heartbeat_event = asyncio.Event()
    runtime.heartbeat_event = heartbeat_event

    async def handler(_: ClaimedJob) -> None:
        await heartbeat_event.wait()

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
        heartbeat_interval_seconds=0.001,
    )

    result = await consumer.handle(
        JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())
    )

    assert result == "succeeded"
    assert runtime.heartbeats
    assert all(heartbeat == claim for heartbeat in runtime.heartbeats)
    assert runtime.succeeded == [claim]


async def test_consumer_cooperatively_cancels_running_handler() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim, heartbeat_results=[True])
    handler_cancelled = asyncio.Event()

    async def handler(_: ClaimedJob) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
        heartbeat_interval_seconds=0.001,
    )

    result = await consumer.handle(
        JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())
    )

    assert result == "cancelled"
    assert handler_cancelled.is_set()
    assert runtime.failed == [(claim, RetryDisposition.CANCELLED)]
    assert runtime.failure_metadata[0]["error_code"] == "job_cancel_requested"


async def test_consumer_honors_cancellation_won_by_completion_race() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim, succeed_status="cancelled")

    async def handler(_: ClaimedJob) -> None:
        return None

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
    )

    result = await consumer.handle(
        JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())
    )

    assert result == "cancelled"


async def test_consumer_classifies_handler_failure() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim)

    async def handler(_: ClaimedJob) -> None:
        raise ValueError("untrusted detail must not be persisted")

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
        classify_error=lambda _: RetryDisposition.PERMANENT,
    )
    message = JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())

    assert await consumer.handle(message) == "failed"
    assert runtime.failed == [(claim, RetryDisposition.PERMANENT)]


async def test_consumer_preserves_sanitized_ingestion_error_code() -> None:
    claim = _claim()
    runtime = FakeRuntime(claim)

    async def handler(_: ClaimedJob) -> None:
        raise DocumentIngestionError("pdf_parse_failed", "PDF could not be parsed", retryable=False)

    consumer = JobDeliveryConsumer(
        runtime=runtime,  # type: ignore[arg-type]
        worker_id="worker-a",
        handler=handler,
        classify_error=lambda _: RetryDisposition.PERMANENT,
    )

    assert (
        await consumer.handle(
            JobMessage(job_id=claim.job_id, tenant_id=claim.tenant_id, event_id=uuid4())
        )
        == "failed"
    )
    assert runtime.failure_metadata[0]["error_code"] == "pdf_parse_failed"
    assert runtime.failure_metadata[0]["error_message"] == "PDF could not be parsed"


def test_celery_app_uses_redis_only_as_broker() -> None:
    app = create_celery_app(WorkerSettings(_env_file=None))

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.task_default_queue == JOB_QUEUE_NAME
    assert app.conf.result_backend is None


def test_document_job_task_is_registered_explicitly() -> None:
    app = create_celery_app(WorkerSettings(_env_file=None))

    register_job_task(app, consumer_factory=lambda: None)  # type: ignore[arg-type]

    assert JOB_TASK_NAME in app.tasks


def test_async_task_runner_reuses_one_event_loop() -> None:
    runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)

    async def loop_identity() -> int:
        return id(asyncio.get_running_loop())

    try:
        assert runner.run(loop_identity()) == runner.run(loop_identity())
    finally:
        runner.close()
        runner.close()
