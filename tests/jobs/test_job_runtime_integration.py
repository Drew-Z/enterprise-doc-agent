from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.jobs import (
    Job,
    JobAttempt,
    JobAttemptStatus,
    JobCreateResult,
    JobIdempotencyConflict,
    JobLeaseLost,
    JobNotFound,
    JobRuntimeService,
    JobStatus,
    OutboxEvent,
    OutboxEventStatus,
    OutboxService,
    RetryDisposition,
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


async def _seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    actor_id = uuid4()
    suffix = uuid4().hex
    async with session_factory.begin() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"M2 tenant {suffix}",
                slug=f"m2-{suffix}",
                quota_bytes=1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"m2-{suffix}@example.test"))
    return tenant_id, actor_id


async def _runtime(
    *, max_attempts: int = 3
) -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    MutableClock,
    JobRuntimeService,
    UUID,
    UUID,
    JobCreateResult,
]:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    clock = MutableClock(datetime(2026, 7, 18, 8, 0, tzinfo=UTC))
    service = JobRuntimeService(
        session_factory=session_factory,
        clock=clock,
        lease_seconds=10,
        retry_base_seconds=2,
        retry_max_seconds=30,
        jitter=lambda cap: cap,
    )
    created = await service.create_job(
        tenant_id=tenant_id,
        actor_id=actor_id,
        job_type="document.ingest",
        idempotency_key=f"ingest:{uuid4()}",
        payload={"document_version_id": str(uuid4())},
        max_attempts=max_attempts,
    )
    return engine, session_factory, clock, service, tenant_id, actor_id, created


@pytest.mark.integration
async def test_job_creation_is_idempotent_and_detects_payload_conflicts() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory)
    service = JobRuntimeService(session_factory=session_factory)
    key = f"job:{uuid4()}"
    try:
        first = await service.create_job(
            tenant_id=tenant_id,
            actor_id=actor_id,
            job_type="document.ingest",
            idempotency_key=key,
            payload={"version": "one"},
        )
        replay = await service.create_job(
            tenant_id=tenant_id,
            actor_id=actor_id,
            job_type="document.ingest",
            idempotency_key=key,
            payload={"version": "one"},
        )

        assert replay.job_id == first.job_id
        assert replay.outbox_event_id == first.outbox_event_id
        assert replay.replayed is True
        with pytest.raises(JobIdempotencyConflict):
            await service.create_job(
                tenant_id=tenant_id,
                actor_id=actor_id,
                job_type="document.ingest",
                idempotency_key=key,
                payload={"version": "two"},
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_duplicate_delivery_has_one_effective_claim() -> None:
    engine, _, _, service, _, _, created = await _runtime()
    try:
        claims = await asyncio.gather(
            service.claim(job_id=created.job_id, worker_id="worker-a"),
            service.claim(job_id=created.job_id, worker_id="worker-b"),
        )

        effective = [claim for claim in claims if claim is not None]
        assert len(effective) == 1
        assert effective[0].attempt_number == 1
        assert effective[0].job_type == "document.ingest"
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_heartbeat_observes_cancel_and_completion_race_stays_cancelled() -> None:
    engine, session_factory, clock, service, tenant_id, actor_id, created = await _runtime()
    try:
        claim = await service.claim(job_id=created.job_id, worker_id="worker-a")
        assert claim is not None

        clock.advance(4)
        assert await service.heartbeat(claim) is False
        assert (
            await service.cancel(
                job_id=created.job_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            == JobStatus.RUNNING.value
        )
        assert await service.heartbeat(claim) is True
        assert await service.succeed(claim) == JobStatus.CANCELLED.value

        async with session_factory() as session:
            job = await session.get(Job, created.job_id)
            attempt = await session.get(JobAttempt, claim.attempt_id)
        assert job is not None and job.status == JobStatus.CANCELLED.value
        assert attempt is not None and attempt.status == JobAttemptStatus.CANCELLED.value
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_expired_lease_is_reclaimed_and_stale_fencing_is_rejected() -> None:
    engine, session_factory, clock, service, _, _, created = await _runtime()
    try:
        first = await service.claim(job_id=created.job_id, worker_id="worker-old")
        assert first is not None
        clock.advance(11)
        second = await service.claim(job_id=created.job_id, worker_id="worker-new")
        assert second is not None
        assert second.attempt_number == 2
        assert second.fencing_token > first.fencing_token

        with pytest.raises(JobLeaseLost):
            await service.succeed(first)
        await service.succeed(second)

        async with session_factory() as session:
            attempts = (
                await session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == created.job_id)
                    .order_by(JobAttempt.attempt_number)
                )
            ).all()
            job = await session.get(Job, created.job_id)
        assert [attempt.status for attempt in attempts] == [
            JobAttemptStatus.ABANDONED.value,
            JobAttemptStatus.SUCCEEDED.value,
        ]
        assert job is not None and job.status == JobStatus.SUCCEEDED.value
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_retry_backoff_dead_manual_retry_and_cancel_are_durable() -> None:
    engine, session_factory, clock, service, tenant_id, actor_id, created = await _runtime(
        max_attempts=2
    )
    outbox = OutboxService(session_factory=session_factory, clock=clock)
    try:
        first = await service.claim(job_id=created.job_id, worker_id="worker-a")
        assert first is not None
        failed = await service.fail(
            first,
            disposition=RetryDisposition.RETRYABLE,
            error_code="dependency_timeout",
            error_message="bounded failure",
            diagnostic_code="grounding.citation_excerpt_not_verbatim",
        )
        assert failed.status == JobStatus.RETRY_WAIT.value
        assert failed.retry_at == clock.value + timedelta(seconds=2)
        first_attempts = await service.list_attempts(
            job_id=created.job_id,
            tenant_id=tenant_id,
        )
        assert first_attempts[0].diagnostic_code == ("grounding.citation_excerpt_not_verbatim")
        assert await service.claim(job_id=created.job_id, worker_id="too-early") is None

        async with session_factory() as session:
            retry_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == created.job_id,
                    OutboxEvent.event_type == "job.retry.requested",
                )
            )
        assert retry_event is not None
        assert retry_event.available_at == failed.retry_at
        assert (
            await outbox.claim(
                publisher_id="retry-publisher",
                event_id=retry_event.id,
            )
            == ()
        )

        clock.advance(2)
        retry_wakeup = await outbox.claim(
            publisher_id="retry-publisher",
            event_id=retry_event.id,
        )
        assert len(retry_wakeup) == 1
        await outbox.mark_published(retry_wakeup[0])
        second = await service.claim(job_id=created.job_id, worker_id="worker-b")
        assert second is not None
        exhausted = await service.fail(
            second,
            disposition=RetryDisposition.RETRYABLE,
            error_code="dependency_timeout",
            error_message="still failing",
        )
        assert exhausted.status == JobStatus.DEAD.value

        other_tenant_id, _ = await _seed_identity(session_factory)
        with pytest.raises(JobNotFound):
            await service.retry_dead(
                job_id=created.job_id,
                tenant_id=other_tenant_id,
                actor_id=actor_id,
            )
        with pytest.raises(JobNotFound):
            await service.cancel(
                job_id=created.job_id,
                tenant_id=other_tenant_id,
                actor_id=actor_id,
            )

        assert (
            await service.retry_dead(
                job_id=created.job_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            == "pending"
        )
        async with session_factory() as session:
            manual_retry_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == created.job_id,
                    OutboxEvent.event_type == "job.manual_retry.requested",
                )
            )
        assert manual_retry_event is not None
        manual_wakeup = await outbox.claim(
            publisher_id="manual-retry-publisher",
            event_id=manual_retry_event.id,
        )
        assert len(manual_wakeup) == 1
        await outbox.mark_published(manual_wakeup[0])

        third = await service.claim(job_id=created.job_id, worker_id="worker-c")
        assert third is not None and third.attempt_number == 3
        assert (
            await service.cancel(
                job_id=created.job_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            == "running"
        )
        cancelled = await service.fail(
            third,
            disposition=RetryDisposition.CANCELLED,
            error_code="cancelled",
            error_message="cancel acknowledged",
        )
        assert cancelled.status == JobStatus.CANCELLED.value

        async with session_factory() as session:
            job = await session.get(Job, created.job_id)
        assert job is not None
        assert job.status == JobStatus.CANCELLED.value
        assert job.max_attempts == 3
        assert job.cancel_requested_at is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_outbox_expired_publication_lease_can_repeat_safely() -> None:
    engine, session_factory, clock, _, _, _, created = await _runtime()
    outbox = OutboxService(session_factory=session_factory, clock=clock, lease_seconds=5)
    try:
        assert created.outbox_event_id is not None
        first_batch = await outbox.claim(
            publisher_id="publisher-old", event_id=created.outbox_event_id
        )
        assert len(first_batch) == 1
        clock.advance(6)
        second_batch = await outbox.claim(
            publisher_id="publisher-new", event_id=created.outbox_event_id
        )
        assert len(second_batch) == 1
        assert second_batch[0].event_id == first_batch[0].event_id

        with pytest.raises(JobLeaseLost):
            await outbox.mark_published(first_batch[0])
        await outbox.mark_published(second_batch[0])

        async with session_factory() as session:
            row = await session.get(OutboxEvent, second_batch[0].event_id)
        assert row is not None
        assert row.aggregate_id == created.job_id
        assert row.status == OutboxEventStatus.PUBLISHED.value
        assert row.attempts == 2
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_outbox_republishes_stale_event_for_pending_job() -> None:
    engine, session_factory, clock, _, _, _, created = await _runtime()
    outbox = OutboxService(
        session_factory=session_factory,
        clock=clock,
        lease_seconds=5,
        recovery_seconds=5,
    )
    try:
        assert created.outbox_event_id is not None
        first = await outbox.claim(publisher_id="publisher-a", event_id=created.outbox_event_id)
        assert len(first) == 1
        await outbox.mark_published(first[0])

        clock.advance(6)
        recovered = await outbox.claim(publisher_id="publisher-b", event_id=created.outbox_event_id)
        assert len(recovered) == 1
        assert recovered[0].event_id == created.outbox_event_id
        await outbox.mark_published(recovered[0])
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_outbox_republishes_latest_event_after_job_lease_expiry() -> None:
    engine, session_factory, clock, service, _, _, created = await _runtime()
    outbox = OutboxService(
        session_factory=session_factory,
        clock=clock,
        lease_seconds=5,
        recovery_seconds=5,
    )
    try:
        assert created.outbox_event_id is not None
        first = await outbox.claim(publisher_id="publisher-a", event_id=created.outbox_event_id)
        assert len(first) == 1
        await outbox.mark_published(first[0])
        claim = await service.claim(job_id=created.job_id, worker_id="worker-a")
        assert claim is not None

        clock.advance(11)
        recovered = await outbox.claim(publisher_id="publisher-b", event_id=created.outbox_event_id)
        assert len(recovered) == 1
        assert recovered[0].event_id == created.outbox_event_id
        await outbox.mark_published(recovered[0])
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_outbox_does_not_recover_superseded_published_event() -> None:
    engine, session_factory, clock, service, _, _, created = await _runtime()
    outbox = OutboxService(
        session_factory=session_factory,
        clock=clock,
        lease_seconds=5,
        recovery_seconds=5,
    )
    try:
        assert created.outbox_event_id is not None
        initial = await outbox.claim(publisher_id="publisher-a", event_id=created.outbox_event_id)
        assert len(initial) == 1
        await outbox.mark_published(initial[0])

        claim = await service.claim(job_id=created.job_id, worker_id="worker-a")
        assert claim is not None
        failure = await service.fail(
            claim,
            disposition=RetryDisposition.RETRYABLE,
            error_code="dependency_timeout",
            error_message="bounded failure",
        )
        assert failure.retry_at is not None

        clock.advance(11)
        assert (
            await outbox.claim(publisher_id="publisher-b", event_id=created.outbox_event_id) == ()
        )
    finally:
        await engine.dispose()
