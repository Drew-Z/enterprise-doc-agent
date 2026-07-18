from __future__ import annotations

import hashlib
import importlib
import json
import logging
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.jobs.models import (
    Job,
    JobAttempt,
    JobAttemptStatus,
    JobEvent,
    JobStatus,
    OutboxEvent,
    OutboxEventStatus,
)

_LOGGER = logging.getLogger("enterprise_doc_core.jobs")


class JobError(Exception):
    """Base class for durable job command failures."""


class JobIdempotencyConflict(JobError):
    pass


class JobNotFound(JobError):
    pass


class JobNotClaimable(JobError):
    pass


class JobLeaseLost(JobError):
    pass


class JobTerminal(JobError):
    pass


class RetryDisposition(StrEnum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobCreateResult:
    job_id: UUID
    outbox_event_id: UUID | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class JobCancellationResult:
    status: str
    changed: bool
    cancellation_requested: bool


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    attempt_id: UUID
    attempt_number: int
    tenant_id: UUID
    actor_id: UUID
    worker_id: str
    lease_token: UUID
    fencing_token: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    event_id: UUID
    aggregate_id: UUID
    tenant_id: UUID
    event_type: str
    payload: dict[str, Any]
    lease_token: UUID
    publisher_id: str


@dataclass(frozen=True, slots=True)
class JobFailureResult:
    status: str
    retry_at: datetime | None
    attempt_id: UUID


@dataclass(frozen=True, slots=True)
class JobStatusResult:
    job_id: UUID
    tenant_id: UUID
    document_version_id: UUID | None
    job_type: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    available_at: datetime
    last_error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class JobAttemptResult:
    attempt_id: UUID
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class JobEventResult:
    event_id: UUID
    seq: int
    event_type: str
    status: str | None
    payload: dict[str, Any]
    created_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _default_jitter(cap_seconds: float) -> float:
    return random.uniform(0.0, cap_seconds)


def request_fingerprint(
    *,
    job_type: str,
    payload: Mapping[str, Any],
    document_version_id: UUID | None,
    max_attempts: int,
    priority: int,
) -> str:
    encoded = json.dumps(
        {
            "document_version_id": str(document_version_id) if document_version_id else None,
            "job_type": job_type,
            "max_attempts": max_attempts,
            "payload": payload,
            "priority": priority,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _next_event_sequence(session: AsyncSession, *, job_id: UUID) -> int:
    current = await session.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    return int(current or 0) + 1


async def _append_job_event(
    session: AsyncSession,
    *,
    job: Job,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    actor_id: UUID | None = None,
) -> JobEvent:
    event = JobEvent(
        tenant_id=job.tenant_id,
        job_id=job.id,
        seq=await _next_event_sequence(session, job_id=job.id),
        event_type=event_type,
        status=job.status,
        actor_id=actor_id,
        payload=dict(payload or {}),
        payload_version=1,
    )
    session.add(event)
    await session.flush()
    return event


async def _enqueue_job_wakeup(
    session: AsyncSession,
    *,
    job: Job,
    event_type: str,
    available_at: datetime,
) -> OutboxEvent:
    event = OutboxEvent(
        tenant_id=job.tenant_id,
        aggregate_id=job.id,
        event_type=event_type,
        payload={
            "job_id": str(job.id),
            "tenant_id": str(job.tenant_id),
            "document_version_id": (
                str(job.document_version_id) if job.document_version_id is not None else None
            ),
        },
        payload_version=1,
        status=OutboxEventStatus.PENDING.value,
        available_at=available_at,
    )
    session.add(event)
    await session.flush()
    return event


async def create_job_records(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    job_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    document_version_id: UUID | None = None,
    max_attempts: int = 3,
    priority: int = 0,
    request_id: str | None = None,
    correlation_id: str | None = None,
    available_at: datetime | None = None,
    outbox_event_type: str | None = "job.created",
) -> JobCreateResult:
    importlib.import_module("enterprise_doc_core.db.metadata")
    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts must be between 1 and 100")
    if len(idempotency_key) == 0 or len(idempotency_key) > 128:
        raise ValueError("idempotency_key must contain 1 to 128 characters")
    fingerprint = request_fingerprint(
        job_type=job_type,
        payload=payload,
        document_version_id=document_version_id,
        max_attempts=max_attempts,
        priority=priority,
    )
    existing = await session.scalar(
        select(Job)
        .where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key)
        .with_for_update()
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise JobIdempotencyConflict()
        event = (
            await session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == existing.id,
                    OutboxEvent.event_type == outbox_event_type,
                )
                .order_by(OutboxEvent.created_at)
            )
            if outbox_event_type is not None
            else None
        )
        return JobCreateResult(existing.id, event.id if event else None, True)

    job = Job(
        tenant_id=tenant_id,
        actor_id=actor_id,
        document_version_id=document_version_id,
        type=job_type,
        status=JobStatus.PENDING.value,
        priority=priority,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        payload=dict(payload),
        max_attempts=max_attempts,
        available_at=available_at or _utcnow(),
        request_id=request_id,
        correlation_id=correlation_id,
    )
    session.add(job)
    await session.flush()
    await _append_job_event(
        session,
        job=job,
        event_type="job.created",
        payload={"job_type": job_type},
        actor_id=actor_id,
    )
    outbox_event: OutboxEvent | None = None
    if outbox_event_type is not None:
        outbox_event = OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=job.id,
            event_type=outbox_event_type,
            payload={
                "job_id": str(job.id),
                "tenant_id": str(tenant_id),
                "document_version_id": str(document_version_id)
                if document_version_id is not None
                else None,
            },
            payload_version=1,
            status=OutboxEventStatus.PENDING.value,
            available_at=available_at or _utcnow(),
        )
        session.add(outbox_event)
        await session.flush()
    return JobCreateResult(job.id, outbox_event.id if outbox_event else None, False)


async def cancel_job_records(
    session: AsyncSession,
    *,
    job_id: UUID,
    tenant_id: UUID,
    now: datetime,
    actor_id: UUID | None = None,
) -> JobCancellationResult:
    job = await session.scalar(
        select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id).with_for_update()
    )
    if job is None:
        raise JobNotFound()
    if job.status in {
        JobStatus.SUCCEEDED.value,
        JobStatus.DEAD.value,
        JobStatus.CANCELLED.value,
    }:
        return JobCancellationResult(job.status, False, False)
    if job.status == JobStatus.RUNNING.value:
        if job.cancel_requested_at is not None:
            return JobCancellationResult(job.status, False, True)
        job.cancel_requested_at = now
        await _append_job_event(
            session,
            job=job,
            event_type="job.cancel_requested",
            actor_id=actor_id,
        )
        return JobCancellationResult(job.status, True, True)
    job.status = JobStatus.CANCELLED.value
    job.finished_at = now
    job.version += 1
    await _append_job_event(session, job=job, event_type="job.cancelled", actor_id=actor_id)
    return JobCancellationResult(job.status, True, False)


class JobRuntimeService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utcnow,
        lease_seconds: float = 60.0,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 300.0,
        jitter: Callable[[float], float] = _default_jitter,
    ) -> None:
        if lease_seconds <= 0 or retry_base_seconds <= 0 or retry_max_seconds <= 0:
            raise ValueError("job timing settings must be positive")
        self.session_factory = session_factory
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.jitter = jitter

    async def create_job(self, **kwargs: Any) -> JobCreateResult:
        kwargs.setdefault("available_at", self.clock())
        async with self.session_factory.begin() as session:
            return await create_job_records(session, **kwargs)

    async def get_status(self, *, job_id: UUID, tenant_id: UUID) -> JobStatusResult:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id)
            )
            if job is None:
                raise JobNotFound()
            return JobStatusResult(
                job_id=job.id,
                tenant_id=job.tenant_id,
                document_version_id=job.document_version_id,
                job_type=job.type,
                status=job.status,
                priority=job.priority,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                available_at=job.available_at,
                last_error_code=job.last_error_code,
                started_at=job.started_at,
                finished_at=job.finished_at,
                cancel_requested=job.cancel_requested_at is not None,
            )

    async def list_attempts(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
    ) -> tuple[JobAttemptResult, ...]:
        async with self.session_factory() as session:
            owned = await session.scalar(
                select(Job.id).where(Job.id == job_id, Job.tenant_id == tenant_id)
            )
            if owned is None:
                raise JobNotFound()
            rows = (
                await session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job_id, JobAttempt.tenant_id == tenant_id)
                    .order_by(JobAttempt.attempt_number)
                )
            ).all()
            return tuple(
                JobAttemptResult(
                    attempt_id=row.id,
                    attempt_number=row.attempt_number,
                    status=row.status,
                    worker_id=row.worker_id,
                    started_at=row.started_at,
                    heartbeat_at=row.heartbeat_at,
                    finished_at=row.finished_at,
                    error_code=row.error_code,
                )
                for row in rows
            )

    async def list_events(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
    ) -> tuple[JobEventResult, ...]:
        async with self.session_factory() as session:
            owned = await session.scalar(
                select(Job.id).where(Job.id == job_id, Job.tenant_id == tenant_id)
            )
            if owned is None:
                raise JobNotFound()
            rows = (
                await session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.tenant_id == tenant_id)
                    .order_by(JobEvent.seq)
                )
            ).all()
            return tuple(
                JobEventResult(
                    event_id=row.id,
                    seq=row.seq,
                    event_type=row.event_type,
                    status=row.status,
                    payload=dict(row.payload),
                    created_at=row.created_at,
                )
                for row in rows
            )

    async def claim(self, *, job_id: UUID, worker_id: str) -> ClaimedJob | None:
        now = self.clock()
        async with self.session_factory.begin() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id).with_for_update(skip_locked=True)
            )
            if job is None or job.status in {
                JobStatus.SUCCEEDED.value,
                JobStatus.DEAD.value,
                JobStatus.CANCELLED.value,
            }:
                return None
            if job.status == JobStatus.RUNNING.value:
                if job.lease_expires_at is not None and job.lease_expires_at > now:
                    return None
                active_attempt = await session.scalar(
                    select(JobAttempt)
                    .where(
                        JobAttempt.job_id == job.id,
                        JobAttempt.attempt_number == job.attempts,
                        JobAttempt.status == JobAttemptStatus.RUNNING.value,
                    )
                    .with_for_update()
                )
                if active_attempt is not None:
                    active_attempt.status = JobAttemptStatus.ABANDONED.value
                    active_attempt.finished_at = now
                await _append_job_event(
                    session,
                    job=job,
                    event_type="job.lease_expired",
                    payload={"worker_id": worker_id},
                )
            elif job.available_at > now:
                return None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.DEAD.value
                job.finished_at = now
                job.last_error_code = "max_attempts_exceeded"
                job.last_error = "The job exhausted its retry budget."
                self._clear_lease(job)
                job.version += 1
                await _append_job_event(session, job=job, event_type="job.dead")
                return None

            lease_token = uuid4()
            fencing_token = job.fencing_token + 1
            attempt_number = job.attempts + 1
            job.attempts = attempt_number
            job.status = JobStatus.RUNNING.value
            job.locked_by = worker_id
            job.lease_token = lease_token
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.heartbeat_at = now
            job.fencing_token = fencing_token
            job.started_at = job.started_at or now
            job.version += 1
            attempt = JobAttempt(
                tenant_id=job.tenant_id,
                job_id=job.id,
                attempt_number=attempt_number,
                status=JobAttemptStatus.RUNNING.value,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                started_at=now,
                heartbeat_at=now,
            )
            session.add(attempt)
            await session.flush()
            await _append_job_event(
                session,
                job=job,
                event_type="job.claimed",
                payload={"attempt_number": attempt_number, "worker_id": worker_id},
            )
            return ClaimedJob(
                job_id=job.id,
                attempt_id=attempt.id,
                attempt_number=attempt_number,
                tenant_id=job.tenant_id,
                actor_id=job.actor_id,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                payload=dict(job.payload),
            )

    async def heartbeat(self, claim: ClaimedJob) -> bool:
        now = self.clock()
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            if not self._owns_lease(job, claim):
                raise JobLeaseLost()
            assert job is not None
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.id == claim.attempt_id)
            )
            if attempt is not None:
                attempt.heartbeat_at = now
            return job.cancel_requested_at is not None

    async def succeed(self, claim: ClaimedJob) -> str:
        now = self.clock()
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            if not self._owns_lease(job, claim):
                raise JobLeaseLost()
            assert job is not None
            cancelled = job.cancel_requested_at is not None
            job.status = JobStatus.CANCELLED.value if cancelled else JobStatus.SUCCEEDED.value
            job.finished_at = now
            self._clear_lease(job)
            job.version += 1
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.id == claim.attempt_id)
            )
            if attempt is not None:
                attempt.status = (
                    JobAttemptStatus.CANCELLED.value
                    if cancelled
                    else JobAttemptStatus.SUCCEEDED.value
                )
                attempt.finished_at = now
            await _append_job_event(
                session,
                job=job,
                event_type="job.cancelled" if cancelled else "job.succeeded",
            )
            return job.status

    async def fail(
        self,
        claim: ClaimedJob,
        *,
        disposition: RetryDisposition,
        error_code: str,
        error_message: str,
        error_class: str = "JobError",
    ) -> JobFailureResult:
        now = self.clock()
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            if not self._owns_lease(job, claim):
                raise JobLeaseLost()
            assert job is not None
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.id == claim.attempt_id)
            )
            if attempt is None:
                raise JobLeaseLost()
            attempt.finished_at = now
            attempt.error_code = error_code[:100]
            attempt.error_class = error_class[:200]
            attempt.error_message = error_message[:1000]
            attempt.retryable = disposition is RetryDisposition.RETRYABLE
            retry_at: datetime | None = None
            if disposition is RetryDisposition.CANCELLED or job.cancel_requested_at is not None:
                attempt.status = JobAttemptStatus.CANCELLED.value
                job.status = JobStatus.CANCELLED.value
                job.finished_at = now
            elif disposition is RetryDisposition.RETRYABLE and job.attempts < job.max_attempts:
                attempt.status = JobAttemptStatus.RETRYABLE_FAILED.value
                cap = min(
                    self.retry_max_seconds, self.retry_base_seconds * (2 ** (job.attempts - 1))
                )
                retry_at = now + timedelta(seconds=self.jitter(cap))
                job.status = JobStatus.RETRY_WAIT.value
                job.available_at = retry_at
                await _enqueue_job_wakeup(
                    session,
                    job=job,
                    event_type="job.retry.requested",
                    available_at=retry_at,
                )
            else:
                attempt.status = JobAttemptStatus.PERMANENT_FAILED.value
                job.status = JobStatus.DEAD.value
                job.finished_at = now
            job.last_error_code = error_code[:100]
            job.last_error = error_message[:1000]
            self._clear_lease(job)
            job.version += 1
            await _append_job_event(
                session,
                job=job,
                event_type=f"job.{job.status}",
                payload={"error_code": error_code[:100]},
            )
            return JobFailureResult(job.status, retry_at, attempt.id)

    async def cancel(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        actor_id: UUID | None = None,
    ) -> str:
        now = self.clock()
        async with self.session_factory.begin() as session:
            result = await cancel_job_records(
                session,
                job_id=job_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                now=now,
            )
            return result.status

    async def retry_dead(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        actor_id: UUID | None = None,
    ) -> str:
        now = self.clock()
        async with self.session_factory.begin() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id).with_for_update()
            )
            if job is None:
                raise JobNotFound()
            if job.status != JobStatus.DEAD.value:
                raise JobNotClaimable()
            job.status = JobStatus.PENDING.value
            job.max_attempts += 1
            job.available_at = now
            job.finished_at = None
            job.last_error_code = None
            job.last_error = None
            job.version += 1
            await _append_job_event(
                session, job=job, event_type="job.manual_retry", actor_id=actor_id
            )
            await _enqueue_job_wakeup(
                session,
                job=job,
                event_type="job.manual_retry.requested",
                available_at=now,
            )
            return job.status

    @staticmethod
    def _owns_lease(job: Job | None, claim: ClaimedJob) -> bool:
        return bool(
            job is not None
            and job.status == JobStatus.RUNNING.value
            and job.locked_by == claim.worker_id
            and job.lease_token == claim.lease_token
            and job.fencing_token == claim.fencing_token
        )

    @staticmethod
    def _clear_lease(job: Job) -> None:
        job.locked_by = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None


class OutboxService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utcnow,
        lease_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.lease_seconds = lease_seconds

    async def claim(
        self,
        *,
        publisher_id: str,
        limit: int = 20,
        event_id: UUID | None = None,
    ) -> tuple[ClaimedOutboxEvent, ...]:
        if limit <= 0:
            return ()
        now = self.clock()
        async with self.session_factory.begin() as session:
            filters = [
                or_(
                    OutboxEvent.status == OutboxEventStatus.PENDING.value,
                    (OutboxEvent.status == OutboxEventStatus.PUBLISHING.value)
                    & (OutboxEvent.lease_expires_at <= now),
                ),
                OutboxEvent.available_at <= now,
            ]
            if event_id is not None:
                filters.append(OutboxEvent.id == event_id)
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(*filters)
                    .order_by(OutboxEvent.available_at, OutboxEvent.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            claimed: list[ClaimedOutboxEvent] = []
            for row in rows:
                token = uuid4()
                row.status = OutboxEventStatus.PUBLISHING.value
                row.attempts += 1
                row.locked_by = publisher_id
                row.lease_token = token
                row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                claimed.append(
                    ClaimedOutboxEvent(
                        event_id=row.id,
                        aggregate_id=row.aggregate_id,
                        tenant_id=row.tenant_id,
                        event_type=row.event_type,
                        payload=dict(row.payload),
                        lease_token=token,
                        publisher_id=publisher_id,
                    )
                )
            return tuple(claimed)

    async def mark_published(self, event: ClaimedOutboxEvent) -> None:
        now = self.clock()
        async with self.session_factory.begin() as session:
            row = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.id == event.event_id).with_for_update()
            )
            if row is None or not (
                row.status == OutboxEventStatus.PUBLISHING.value
                and row.locked_by == event.publisher_id
                and row.lease_token == event.lease_token
            ):
                raise JobLeaseLost()
            row.status = OutboxEventStatus.PUBLISHED.value
            row.published_at = now
            row.locked_by = None
            row.lease_token = None
            row.lease_expires_at = None
