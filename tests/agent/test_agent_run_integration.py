from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import enterprise_doc_core.agents.service as agent_service_module
from enterprise_doc_core.agents import (
    AgentArtifact,
    AgentDocumentVersionNotReady,
    AgentPrincipalForbidden,
    AgentRun,
    AgentRunEvent,
    AgentRunEvidence,
    AgentRunExecution,
    AgentRunIdempotencyConflict,
    AgentRunNotFound,
    AgentRunService,
    AgentRunStatus,
    AgentRunTaskType,
    CreateAgentRunInput,
    ToolExecution,
)
from enterprise_doc_core.config import (
    AgentSettings,
    DatabaseSettings,
    ModelSettings,
)
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    Document,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.jobs import (
    Job,
    JobAttempt,
    JobAttemptStatus,
    JobRuntimeService,
    JobStatus,
    OutboxEvent,
    OutboxEventStatus,
    RetryDisposition,
)
from enterprise_doc_core.uploads import UploadSession, UploadSessionStatus
from enterprise_doc_worker.agent_handler import (
    agent_failure_lock_key,
    project_agent_run_failure,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class SeededAgentContext:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    document_id: UUID
    document_version_id: UUID
    generation_id: UUID
    filename: str

    @property
    def principal(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role="owner",
        )


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    clock: Any | None = None,
) -> AgentRunService:
    kwargs: dict[str, Any] = {
        "session_factory": session_factory,
        "agent_settings": AgentSettings(),
        "model_settings": ModelSettings(),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return AgentRunService(**kwargs)


def _request(
    context: SeededAgentContext,
    *,
    input_text: str = "What are the payment terms?",
    publish_requested: bool = False,
) -> CreateAgentRunInput:
    return CreateAgentRunInput(
        document_version_id=context.document_version_id,
        task_type=AgentRunTaskType.QUESTION_ANSWER,
        input_text=input_text,
        extraction_schema=None,
        publish_requested=publish_requested,
    )


async def _seed_agent_context(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    membership_active: bool = True,
    version_status: DocumentVersionStatus = DocumentVersionStatus.READY,
    generation_active: bool = True,
    generation_status: DocumentIngestionStatus = DocumentIngestionStatus.SUCCEEDED,
    generation_stage: DocumentIngestionStage = DocumentIngestionStage.READY,
) -> SeededAgentContext:
    tenant_id = uuid4()
    actor_id = uuid4()
    membership_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    generation_id = uuid4()
    upload_id = uuid4()
    suffix = uuid4().hex
    filename = f"contract-{suffix}.txt"
    content = b"Payment is due within 30 days."
    content_sha256 = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC)

    async with session_factory.begin() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"M4 Agent tenant {suffix}",
                slug=f"m4-agent-{suffix}",
                quota_bytes=1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"m4-agent-{suffix}@example.test"))
        await session.flush()
        session.add(
            Membership(
                id=membership_id,
                tenant_id=tenant_id,
                user_id=actor_id,
                role="owner",
                is_active=membership_active,
            )
        )
        upload = UploadSession(
            id=upload_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            pending_document_id=document_id,
            pending_version_id=version_id,
            status=UploadSessionStatus.COMPLETED.value,
            idempotency_key=f"m4-upload:{suffix}",
            request_fingerprint=content_sha256,
            object_key=f"{tenant_id}/documents/{version_id}/{filename}",
            original_filename=filename,
            extension=".txt",
            declared_media_type="text/plain",
            size_bytes=len(content),
            declared_sha256=content_sha256,
            part_size_bytes=len(content),
            expected_part_count=1,
            reserved_bytes=0,
            expires_at=now + timedelta(hours=1),
            completed_at=now,
        )
        session.add(upload)
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                created_by=actor_id,
                title=filename,
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                tenant_id=tenant_id,
                document_id=document_id,
                upload_session_id=upload_id,
                version_number=1,
                status=version_status.value,
                object_key=upload.object_key,
                original_filename=filename,
                declared_media_type="text/plain",
                detected_media_type="text/plain",
                size_bytes=len(content),
                declared_sha256=content_sha256,
                content_sha256_verified_at=now,
                created_by=actor_id,
            )
        )
        await session.flush()
        upload.document_version_id = version_id
        session.add(
            DocumentIngestionGeneration(
                id=generation_id,
                tenant_id=tenant_id,
                document_version_id=version_id,
                parser_version=1,
                chunker_version=1,
                embedding_version=1,
                embedding_model="controlled",
                embedding_dimension=8,
                status=generation_status.value,
                stage=generation_stage.value,
                chunk_count=0,
                embedded_count=0,
                active=generation_active,
                started_at=now,
                finished_at=now,
            )
        )

    return SeededAgentContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        membership_id=membership_id,
        document_id=document_id,
        document_version_id=version_id,
        generation_id=generation_id,
        filename=filename,
    )


async def _tenant_count(
    session: AsyncSession,
    model: type[Any],
    tenant_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    )
    assert value is not None
    return value


async def _runtime() -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    SeededAgentContext,
    AgentRunService,
]:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    return engine, session_factory, context, _service(session_factory)


async def test_create_persists_only_the_initial_transaction_boundary() -> None:
    engine, session_factory, context, service = await _runtime()
    request = _request(context, input_text="  What are the payment terms?  ")
    try:
        result = await service.create(
            principal=context.principal,
            idempotency_key="agent-create-initial",
            request=request,
            request_id="request-m4-initial",
            correlation_id="correlation-m4-initial",
        )

        async with session_factory() as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == result.run_id))
            execution = await session.scalar(
                select(AgentRunExecution).where(AgentRunExecution.run_id == result.run_id)
            )
            event = await session.scalar(
                select(AgentRunEvent).where(AgentRunEvent.run_id == result.run_id)
            )
            job = await session.scalar(select(Job).where(Job.id == result.job_id))
            outbox = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == result.job_id)
            )

            assert run is not None
            assert execution is not None
            assert event is not None
            assert job is not None
            assert outbox is not None
            assert result.replayed is False
            assert result.status == AgentRunStatus.PENDING.value
            assert result.created_at == run.created_at
            assert run.actor_id == context.actor_id
            assert run.document_version_id == context.document_version_id
            assert run.index_generation_id == context.generation_id
            assert run.input_text == "What are the payment terms?"
            assert run.graph_thread_id == str(run.id)
            assert run.next_event_seq == 2
            assert run.current_execution_seq == 0
            assert len(run.request_fingerprint) == 64
            assert execution.sequence == 0
            assert execution.kind == "initial"
            assert execution.job_id == job.id
            assert job.type == "agent.execute"
            assert job.status == JobStatus.PENDING.value
            assert job.idempotency_key == f"agent:{run.id}:execution:0"
            assert job.request_id == "request-m4-initial"
            assert job.correlation_id == "correlation-m4-initial"
            assert job.payload == {
                "payload_version": 1,
                "run_id": str(run.id),
                "execution_sequence": 0,
                "graph_thread_id": str(run.id),
                "graph_version": "m4.v1",
            }
            assert outbox.event_type == "agent.execute.requested"
            assert outbox.status == OutboxEventStatus.PENDING.value
            assert event.seq == 1
            assert event.event_type == "run.created"
            assert event.public_payload == {
                "task_type": "question_answer",
                "document_version_id": str(context.document_version_id),
                "publish_requested": False,
            }
            assert await _tenant_count(session, ToolExecution, context.tenant_id) == 0
            assert await _tenant_count(session, AgentRunEvidence, context.tenant_id) == 0
            assert await _tenant_count(session, AgentArtifact, context.tenant_id) == 0
    finally:
        await engine.dispose()


async def test_replay_survives_readiness_change_and_conflicts_on_changed_payload() -> None:
    engine, session_factory, context, service = await _runtime()
    key = "agent-replay-readiness-change"
    try:
        first = await service.create(
            principal=context.principal,
            idempotency_key=key,
            request=_request(context, input_text="  Summarize payment terms  "),
        )
        async with session_factory.begin() as session:
            generation = await session.get(DocumentIngestionGeneration, context.generation_id)
            version = await session.get(DocumentVersion, context.document_version_id)
            assert generation is not None
            assert version is not None
            generation.active = False
            version.status = DocumentVersionStatus.FAILED.value

        replay = await service.create(
            principal=context.principal,
            idempotency_key=key,
            request=_request(context, input_text="Summarize payment terms"),
        )

        assert replay.run_id == first.run_id
        assert replay.job_id == first.job_id
        assert replay.replayed is True
        with pytest.raises(AgentRunIdempotencyConflict):
            await service.create(
                principal=context.principal,
                idempotency_key=key,
                request=_request(context, input_text="Summarize delivery terms"),
            )

        async with session_factory() as session:
            assert await _tenant_count(session, AgentRun, context.tenant_id) == 1
            assert await _tenant_count(session, AgentRunExecution, context.tenant_id) == 1
            assert await _tenant_count(session, AgentRunEvent, context.tenant_id) == 1
            assert await _tenant_count(session, Job, context.tenant_id) == 1
            assert await _tenant_count(session, OutboxEvent, context.tenant_id) == 1
    finally:
        await engine.dispose()


async def test_concurrent_create_has_one_effective_transaction_per_tenant() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    first_context = await _seed_agent_context(session_factory)
    second_context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    key = "agent-concurrent-create"
    try:
        results = await asyncio.gather(
            service.create(
                principal=first_context.principal,
                idempotency_key=key,
                request=_request(first_context),
            ),
            service.create(
                principal=first_context.principal,
                idempotency_key=key,
                request=_request(first_context),
            ),
        )
        cross_tenant = await service.create(
            principal=second_context.principal,
            idempotency_key=key,
            request=_request(second_context),
        )

        assert results[0].run_id == results[1].run_id
        assert results[0].job_id == results[1].job_id
        assert sorted(result.replayed for result in results) == [False, True]
        assert cross_tenant.run_id != results[0].run_id
        assert cross_tenant.job_id != results[0].job_id

        async with session_factory() as session:
            for context in (first_context, second_context):
                assert await _tenant_count(session, AgentRun, context.tenant_id) == 1
                assert await _tenant_count(session, AgentRunExecution, context.tenant_id) == 1
                assert await _tenant_count(session, AgentRunEvent, context.tenant_id) == 1
                assert await _tenant_count(session, Job, context.tenant_id) == 1
                assert await _tenant_count(session, OutboxEvent, context.tenant_id) == 1
    finally:
        await engine.dispose()


async def test_membership_readiness_and_ready_listing_are_tenant_scoped() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    first_context = await _seed_agent_context(session_factory)
    second_context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    try:
        ready = await service.list_ready_document_versions(tenant_id=first_context.tenant_id)
        assert [item.version_id for item in ready] == [first_context.document_version_id]

        with pytest.raises(AgentDocumentVersionNotReady):
            await service.create(
                principal=first_context.principal,
                idempotency_key="agent-cross-tenant-version",
                request=CreateAgentRunInput(
                    document_version_id=second_context.document_version_id,
                    task_type=AgentRunTaskType.SUMMARY,
                    input_text="Summarize this contract",
                    extraction_schema=None,
                    publish_requested=False,
                ),
            )

        async with session_factory.begin() as session:
            membership = await session.get(Membership, first_context.membership_id)
            assert membership is not None
            membership.is_active = False
        with pytest.raises(AgentPrincipalForbidden):
            await service.create(
                principal=first_context.principal,
                idempotency_key="agent-inactive-membership",
                request=_request(first_context),
            )

        async with session_factory.begin() as session:
            membership = await session.get(Membership, first_context.membership_id)
            version = await session.get(DocumentVersion, first_context.document_version_id)
            assert membership is not None
            assert version is not None
            membership.is_active = True
            version.status = DocumentVersionStatus.UPLOADED.value
        with pytest.raises(AgentDocumentVersionNotReady):
            await service.create(
                principal=first_context.principal,
                idempotency_key="agent-version-not-ready",
                request=_request(first_context),
            )

        assert await service.list_ready_document_versions(tenant_id=first_context.tenant_id) == ()
        async with session_factory() as session:
            assert await _tenant_count(session, AgentRun, first_context.tenant_id) == 0
            assert await _tenant_count(session, Job, first_context.tenant_id) == 0
            assert await _tenant_count(session, OutboxEvent, first_context.tenant_id) == 0
    finally:
        await engine.dispose()


async def test_pending_cancel_is_atomic_idempotent_and_events_are_contiguous() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    other_context = await _seed_agent_context(session_factory)
    cancelled_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    service = _service(session_factory, clock=lambda: cancelled_at)
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key="agent-pending-cancel",
            request=_request(context),
        )
        status = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
        assert status.status == AgentRunStatus.PENDING.value
        assert len(status.executions) == 1
        assert status.executions[0].job_id == created.job_id
        assert status.executions[0].job_status == JobStatus.PENDING.value
        assert (
            await service.list_events(
                run_id=created.run_id,
                tenant_id=context.tenant_id,
                after_seq=1,
            )
            == ()
        )

        with pytest.raises(AgentRunNotFound):
            await service.get_status(run_id=created.run_id, tenant_id=other_context.tenant_id)
        with pytest.raises(AgentRunNotFound):
            await service.list_events(
                run_id=created.run_id,
                tenant_id=other_context.tenant_id,
            )
        with pytest.raises(AgentRunNotFound):
            await service.cancel(
                run_id=created.run_id,
                tenant_id=other_context.tenant_id,
                actor_id=other_context.actor_id,
            )

        first_cancel = await service.cancel(
            run_id=created.run_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
        )
        replayed_cancel = await service.cancel(
            run_id=created.run_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
        )

        assert first_cancel.status == AgentRunStatus.CANCELLED.value
        assert replayed_cancel.status == AgentRunStatus.CANCELLED.value
        assert first_cancel.cancelled_at == cancelled_at
        events = await service.list_events(run_id=created.run_id, tenant_id=context.tenant_id)
        assert [event.seq for event in events] == [1, 2]
        assert [event.event_type for event in events] == ["run.created", "run.cancelled"]
        assert events[1].public_payload == {"status": "cancelled"}

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            job = await session.get(Job, created.job_id)
            assert run is not None
            assert job is not None
            assert run.status == AgentRunStatus.CANCELLED.value
            assert run.cancelled_at == cancelled_at
            assert run.finished_at == cancelled_at
            assert run.next_event_seq == 3
            assert job.status == JobStatus.CANCELLED.value
            assert job.finished_at == cancelled_at
            assert await _tenant_count(session, AgentRunEvent, context.tenant_id) == 2
    finally:
        await engine.dispose()


async def test_member_cannot_cancel_another_actor_run_but_owner_can_override() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    other_actor_id = uuid4()
    other_membership_id = uuid4()
    try:
        async with session_factory.begin() as session:
            session.add(User(id=other_actor_id, email=f"member-{uuid4().hex}@example.test"))
            await session.flush()
            session.add(
                Membership(
                    id=other_membership_id,
                    tenant_id=context.tenant_id,
                    user_id=other_actor_id,
                    role=MembershipRole.MEMBER.value,
                    is_active=True,
                )
            )

        service = _service(session_factory)
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"member-cancel:{uuid4().hex}",
            request=_request(context),
        )
        with pytest.raises(AgentPrincipalForbidden):
            await service.cancel(
                run_id=created.run_id,
                tenant_id=context.tenant_id,
                actor_id=other_actor_id,
            )

        owner_cancelled = await service.cancel(
            run_id=created.run_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
        )
        assert owner_cancelled.status == AgentRunStatus.CANCELLED.value
    finally:
        await engine.dispose()


async def test_terminal_agent_job_failure_projects_run_failed_atomically() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    runtime = JobRuntimeService(
        session_factory=session_factory,
        failure_lock_key=agent_failure_lock_key,
        failure_projector=project_agent_run_failure,
    )
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"terminal-agent-failure:{uuid4().hex}",
            request=_request(context),
        )
        claim = await runtime.claim(job_id=created.job_id, worker_id="failing-agent-worker")
        assert claim is not None
        failure = await runtime.fail(
            claim,
            disposition=RetryDisposition.PERMANENT,
            error_code="agent_graph_result_invalid",
            error_message="The Agent execution failed.",
        )
        assert failure.status == JobStatus.DEAD.value

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            job = await session.get(Job, created.job_id)
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == created.run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()
        assert run is not None and run.status == AgentRunStatus.FAILED.value
        assert run.error_code == "agent_graph_result_invalid"
        assert run.finished_at is not None
        assert job is not None and job.status == JobStatus.DEAD.value
        assert events[-1].event_type == "run.finished"
        assert events[-1].public_payload == {
            "status": "failed",
            "refusal_reason": None,
        }
    finally:
        await engine.dispose()


async def test_expired_final_agent_lease_projects_run_failed() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    clock = MutableClock(datetime.now(UTC))
    service = _service(session_factory, clock=clock)
    runtime = JobRuntimeService(
        session_factory=session_factory,
        clock=clock,
        lease_seconds=10,
        failure_lock_key=agent_failure_lock_key,
        failure_projector=project_agent_run_failure,
    )
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"expired-final-agent-lease:{uuid4().hex}",
            request=_request(context),
        )
        # Job creation uses the database clock for availability; move the test
        # clock past that timestamp before the first claim.
        clock.advance(2)
        async with session_factory.begin() as session:
            job = await session.get(Job, created.job_id)
            assert job is not None
            job.max_attempts = 1

        claim = await runtime.claim(job_id=created.job_id, worker_id="crashed-agent-worker")
        assert claim is not None
        clock.advance(11)
        assert await runtime.claim(job_id=created.job_id, worker_id="reclaimer") is None

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            job = await session.get(Job, created.job_id)
            attempt = await session.get(JobAttempt, claim.attempt_id)
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == created.run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()
        assert run is not None and run.status == AgentRunStatus.FAILED.value
        assert run.error_code == "max_attempts_exceeded"
        assert run.finished_at == clock.value
        assert job is not None and job.status == JobStatus.DEAD.value
        assert attempt is not None and attempt.status == JobAttemptStatus.ABANDONED.value
        assert events[-1].event_type == "run.finished"
        assert events[-1].public_payload == {
            "status": AgentRunStatus.FAILED.value,
            "refusal_reason": None,
        }
    finally:
        await engine.dispose()


async def test_running_job_cancel_projects_attempts_and_cooperates_with_worker() -> None:
    engine, session_factory, context, service = await _runtime()
    job_service = JobRuntimeService(session_factory=session_factory)
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key="agent-running-cancel",
            request=_request(context),
        )
        claim = await job_service.claim(job_id=created.job_id, worker_id="agent-worker-1")
        assert claim is not None

        running = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
        assert running.executions[0].job_status == JobStatus.RUNNING.value
        assert running.executions[0].attempts == 1
        assert running.executions[0].attempt_history[0].worker_id == "agent-worker-1"

        cancelled = await service.cancel(
            run_id=created.run_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
        )
        assert cancelled.status == AgentRunStatus.CANCELLED.value
        assert cancelled.executions[0].job_status == JobStatus.RUNNING.value
        assert cancelled.executions[0].cancel_requested is True
        assert await job_service.heartbeat(claim) is True
        assert await job_service.succeed(claim) == JobStatus.CANCELLED.value

        completed = await service.get_status(run_id=created.run_id, tenant_id=context.tenant_id)
        assert completed.status == AgentRunStatus.CANCELLED.value
        assert completed.executions[0].job_status == JobStatus.CANCELLED.value
        assert completed.executions[0].attempt_history[0].status == "cancelled"
    finally:
        await engine.dispose()


async def test_late_creation_failure_rolls_back_run_job_outbox_and_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory, context, service = await _runtime()

    async def fail_event_append(*_: Any, **__: Any) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(agent_service_module, "append_agent_run_event", fail_event_append)
    try:
        with pytest.raises(RuntimeError, match="injected event failure"):
            await service.create(
                principal=context.principal,
                idempotency_key="agent-rollback-on-event-failure",
                request=_request(context),
            )

        async with session_factory() as session:
            assert await _tenant_count(session, AgentRun, context.tenant_id) == 0
            assert await _tenant_count(session, AgentRunExecution, context.tenant_id) == 0
            assert await _tenant_count(session, AgentRunEvent, context.tenant_id) == 0
            assert await _tenant_count(session, Job, context.tenant_id) == 0
            assert await _tenant_count(session, OutboxEvent, context.tenant_id) == 0
    finally:
        await engine.dispose()
