from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.events import public_event_payload
from enterprise_doc_core.agents.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunExecution,
    AgentRunExecutionKind,
    AgentRunStatus,
    AgentRunTaskType,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from enterprise_doc_core.agents.state import (
    AgentRunTransitionEvent,
    ApprovalRequestEvent,
    transition_agent_run,
    transition_approval_request,
)
from enterprise_doc_core.config import AgentSettings, ModelProvider, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents.models import (
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.identity.models import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.jobs import cancel_job_records, create_job_records
from enterprise_doc_core.jobs.models import Job, JobAttempt


class AgentRunError(Exception):
    code = "agent_run_error"
    message = "The Agent run request could not be completed."

    def __init__(self) -> None:
        super().__init__(self.message)


class AgentRunIdempotencyConflict(AgentRunError):
    code = "agent_run_idempotency_conflict"
    message = "The idempotency key is already bound to a different Agent run request."


class AgentRunNotFound(AgentRunError):
    code = "agent_run_not_found"
    message = "The Agent run was not found."


class AgentDocumentVersionNotReady(AgentRunError):
    code = "agent_document_version_not_ready"
    message = "The requested document version is not available for Agent execution."


class AgentPrincipalForbidden(AgentRunError):
    code = "agent_principal_forbidden"
    message = "The current principal cannot manage Agent runs for this tenant."


class AgentRunInputInvalid(AgentRunError):
    code = "agent_run_input_invalid"
    message = "The Agent run input is invalid."


class AgentRunIntegrityError(AgentRunError):
    code = "agent_run_integrity_error"
    message = "The Agent run persistence state is incomplete."


@dataclass(frozen=True, slots=True)
class CreateAgentRunInput:
    document_version_id: UUID
    task_type: AgentRunTaskType | str
    input_text: str
    extraction_schema: dict[str, Any] | None
    publish_requested: bool


@dataclass(frozen=True, slots=True)
class CreateAgentRunResult:
    run_id: UUID
    job_id: UUID
    status: str
    replayed: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentRunAttemptResult:
    attempt_id: UUID
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    diagnostic_code: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunExecutionResult:
    execution_id: UUID
    sequence: int
    kind: str
    job_id: UUID
    job_status: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    attempt_history: tuple[AgentRunAttemptResult, ...]


@dataclass(frozen=True, slots=True)
class AgentRunStatusResult:
    run_id: UUID
    tenant_id: UUID
    document_version_id: UUID
    task_type: str
    publish_requested: bool
    status: str
    graph_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    model_version: str | None
    tool_schema_version: str
    current_execution_seq: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    waiting_at: datetime | None
    finished_at: datetime | None
    cancelled_at: datetime | None
    executions: tuple[AgentRunExecutionResult, ...]
    model_revision: str | None = None
    fallback_trigger_code: str | None = None
    provider_request_count: int | None = None
    provider_usage_request_count: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    repair_request_count: int | None = None
    fallback_count: int | None = None
    breaker_state: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunEventResult:
    event_id: UUID
    seq: int
    event_type: str
    event_version: int
    public_payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReadyDocumentVersionResult:
    version_id: UUID
    document_id: UUID
    generation_id: UUID
    filename: str
    size_bytes: int
    content_sha256: str
    created_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _model_identity(model: ModelSettings) -> tuple[str, str, str | None, str | None]:
    if model.provider is ModelProvider.DETERMINISTIC:
        return (
            model.provider.value,
            "deterministic-grounded",
            model.model_version,
            model.model_revision,
        )
    if model.model_name is None:
        raise AgentRunInputInvalid()
    return model.provider.value, model.model_name, model.model_version, model.model_revision


def agent_run_fingerprint(
    *,
    request: CreateAgentRunInput,
    agent: AgentSettings,
    model: ModelSettings,
) -> str:
    model_provider, model_name, model_version, model_revision = _model_identity(model)
    encoded = json.dumps(
        {
            "document_version_id": str(request.document_version_id),
            "extraction_schema": request.extraction_schema,
            "graph_version": agent.graph_version,
            "input_text": request.input_text.strip(),
            "model_name": model_name,
            "model_provider": model_provider,
            "model_revision": model_revision,
            "model_version": model_version,
            "prompt_version": agent.prompt_version,
            "publish_requested": request.publish_requested,
            "task_type": AgentRunTaskType(request.task_type).value,
            "tool_schema_version": agent.tool_schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def append_agent_run_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    run_id: UUID,
    event_type: str,
    payload: Mapping[str, Any],
    actor_id: UUID | None = None,
) -> AgentRunEvent:
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
        .with_for_update()
    )
    if run is None:
        raise AgentRunNotFound()
    event = AgentRunEvent(
        tenant_id=tenant_id,
        run_id=run_id,
        seq=run.next_event_seq,
        event_type=event_type,
        event_version=1,
        actor_id=actor_id,
        public_payload=public_event_payload(event_type, payload),
    )
    run.next_event_seq += 1
    session.add(event)
    await session.flush()
    return event


class AgentRunService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        agent_settings: AgentSettings,
        model_settings: ModelSettings,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.agent_settings = agent_settings
        self.model_settings = model_settings
        self.clock = clock

    async def create(
        self,
        *,
        principal: PrincipalContext,
        idempotency_key: str,
        request: CreateAgentRunInput,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> CreateAgentRunResult:
        tenant_id, actor_id = self._principal_ids(principal)
        task_type = self._validate_create_input(idempotency_key=idempotency_key, request=request)
        fingerprint = agent_run_fingerprint(
            request=request,
            agent=self.agent_settings,
            model=self.model_settings,
        )
        model_provider, model_name, model_version, model_revision = _model_identity(
            self.model_settings
        )
        async with self.session_factory.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{tenant_id}:{idempotency_key}"},
            )
            await self._require_active_membership(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            existing = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise AgentRunIdempotencyConflict()
                execution = await session.scalar(
                    select(AgentRunExecution).where(
                        AgentRunExecution.run_id == existing.id,
                        AgentRunExecution.sequence == 0,
                    )
                )
                if execution is None:
                    raise AgentRunIntegrityError()
                return CreateAgentRunResult(
                    run_id=existing.id,
                    job_id=execution.job_id,
                    status=existing.status,
                    replayed=True,
                    created_at=existing.created_at,
                )

            generation_id = await self._ready_generation_id(
                session,
                tenant_id=tenant_id,
                document_version_id=request.document_version_id,
            )

            run_id = uuid4()
            run = AgentRun(
                id=run_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=request.document_version_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                task_type=task_type.value,
                input_text=request.input_text.strip(),
                extraction_schema=request.extraction_schema,
                publish_requested=request.publish_requested,
                status=AgentRunStatus.PENDING.value,
                graph_thread_id=str(run_id),
                graph_version=self.agent_settings.graph_version,
                prompt_version=self.agent_settings.prompt_version,
                model_provider=model_provider,
                model_name=model_name,
                model_version=model_version,
                model_revision=model_revision,
                tool_schema_version=self.agent_settings.tool_schema_version,
                index_generation_id=generation_id,
                next_event_seq=1,
                current_execution_seq=0,
            )
            session.add(run)
            await session.flush()
            job_result = await create_job_records(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                job_type="agent.execute",
                idempotency_key=f"agent:{run_id}:execution:0",
                payload={
                    "payload_version": 1,
                    "run_id": str(run_id),
                    "execution_sequence": 0,
                    "graph_thread_id": str(run_id),
                    "graph_version": self.agent_settings.graph_version,
                },
                document_version_id=None,
                max_attempts=self.agent_settings.execution_max_attempts,
                request_id=request_id,
                correlation_id=correlation_id,
                outbox_event_type="agent.execute.requested",
            )
            session.add(
                AgentRunExecution(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    sequence=0,
                    job_id=job_result.job_id,
                    kind=AgentRunExecutionKind.INITIAL.value,
                )
            )
            await append_agent_run_event(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                event_type="run.created",
                payload={
                    "task_type": task_type,
                    "document_version_id": request.document_version_id,
                    "publish_requested": request.publish_requested,
                },
                actor_id=actor_id,
            )
            await session.refresh(run, attribute_names=["created_at"])
            return CreateAgentRunResult(
                run_id=run_id,
                job_id=job_result.job_id,
                status=run.status,
                replayed=False,
                created_at=run.created_at,
            )

    async def get_status(self, *, run_id: UUID, tenant_id: UUID) -> AgentRunStatusResult:
        async with self.session_factory() as session:
            run = await self._get_run(session, run_id=run_id, tenant_id=tenant_id)
            return await self._status_result(session, run)

    async def list_events(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        after_seq: int = 0,
        limit: int = 100,
    ) -> tuple[AgentRunEventResult, ...]:
        if after_seq < 0 or not 1 <= limit <= 500:
            raise AgentRunInputInvalid()
        async with self.session_factory() as session:
            await self._get_run(session, run_id=run_id, tenant_id=tenant_id)
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.run_id == run_id,
                        AgentRunEvent.tenant_id == tenant_id,
                        AgentRunEvent.seq > after_seq,
                    )
                    .order_by(AgentRunEvent.seq)
                    .limit(limit)
                )
            ).all()
            return tuple(
                AgentRunEventResult(
                    event_id=event.id,
                    seq=event.seq,
                    event_type=event.event_type,
                    event_version=event.event_version,
                    public_payload=event.public_payload,
                    created_at=event.created_at,
                )
                for event in events
            )

    async def list_ready_document_versions(
        self,
        *,
        tenant_id: UUID,
    ) -> tuple[ReadyDocumentVersionResult, ...]:
        statement = (
            select(DocumentVersion, DocumentIngestionGeneration)
            .join(
                DocumentIngestionGeneration,
                DocumentIngestionGeneration.document_version_id == DocumentVersion.id,
            )
            .where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.status == DocumentVersionStatus.READY.value,
                DocumentIngestionGeneration.tenant_id == tenant_id,
                DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                DocumentIngestionGeneration.active.is_(True),
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(100)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
            return tuple(
                ReadyDocumentVersionResult(
                    version_id=version.id,
                    document_id=version.document_id,
                    generation_id=generation.id,
                    filename=version.original_filename,
                    size_bytes=version.size_bytes,
                    content_sha256=version.declared_sha256,
                    created_at=version.created_at,
                )
                for version, generation in rows
            )

    async def cancel(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> AgentRunStatusResult:
        now = self.clock()
        async with self.session_factory.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"agent-run:{tenant_id}:{run_id}"},
            )
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
                .with_for_update()
            )
            if run is None:
                raise AgentRunNotFound()
            await self._require_cancel_permission(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_actor_id=run.actor_id,
            )
            if run.status in {
                AgentRunStatus.SUCCEEDED.value,
                AgentRunStatus.REFUSED.value,
                AgentRunStatus.FAILED.value,
                AgentRunStatus.CANCELLED.value,
                AgentRunStatus.REJECTED.value,
                AgentRunStatus.EXPIRED.value,
            }:
                return await self._status_result(session, run)
            execution = await session.scalar(
                select(AgentRunExecution)
                .where(
                    AgentRunExecution.run_id == run.id,
                    AgentRunExecution.sequence == run.current_execution_seq,
                )
                .with_for_update()
            )
            if execution is None:
                raise AgentRunIntegrityError()
            approval = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.run_id == run.id,
                    ApprovalRequest.status.in_(
                        (
                            ApprovalRequestStatus.PENDING.value,
                            ApprovalRequestStatus.APPROVED.value,
                        )
                    ),
                )
                .with_for_update()
            )
            if approval is not None:
                approval.status = transition_approval_request(
                    ApprovalRequestStatus(approval.status),
                    ApprovalRequestEvent.REVOKE,
                ).value
                approval.revoked_at = now
            await cancel_job_records(
                session,
                job_id=execution.job_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                now=now,
            )
            run.status = transition_agent_run(
                AgentRunStatus(run.status), AgentRunTransitionEvent.CANCEL
            ).value
            run.cancelled_at = now
            run.finished_at = now
            await append_agent_run_event(
                session,
                tenant_id=tenant_id,
                run_id=run.id,
                event_type="run.cancelled",
                payload={"status": AgentRunStatus.CANCELLED.value},
                actor_id=actor_id,
            )
            return await self._status_result(session, run)

    @staticmethod
    def _principal_ids(principal: PrincipalContext) -> tuple[UUID, UUID]:
        try:
            return UUID(principal.tenant_id), UUID(principal.actor_id)
        except ValueError as error:
            raise AgentPrincipalForbidden() from error

    @staticmethod
    def _validate_create_input(
        *,
        idempotency_key: str,
        request: CreateAgentRunInput,
    ) -> AgentRunTaskType:
        if not 1 <= len(idempotency_key) <= 128:
            raise AgentRunInputInvalid()
        input_text = request.input_text.strip()
        if not input_text or len(input_text) > 20_000:
            raise AgentRunInputInvalid()
        try:
            task_type = AgentRunTaskType(request.task_type)
        except ValueError as error:
            raise AgentRunInputInvalid() from error
        if task_type is AgentRunTaskType.STRUCTURED_EXTRACTION:
            if request.extraction_schema is None:
                raise AgentRunInputInvalid()
        elif request.extraction_schema is not None:
            raise AgentRunInputInvalid()
        return task_type

    @staticmethod
    async def _require_active_membership(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> None:
        membership_id = await session.scalar(
            select(Membership.id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        if membership_id is None:
            raise AgentPrincipalForbidden()

    @staticmethod
    async def _require_cancel_permission(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_actor_id: UUID,
    ) -> None:
        role = await session.scalar(
            select(Membership.role)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        if role is None or (actor_id != run_actor_id and role != MembershipRole.OWNER.value):
            raise AgentPrincipalForbidden()

    @staticmethod
    async def _ready_generation_id(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
    ) -> UUID:
        generation_id = await session.scalar(
            select(DocumentIngestionGeneration.id)
            .join(
                DocumentVersion,
                DocumentVersion.id == DocumentIngestionGeneration.document_version_id,
            )
            .where(
                DocumentVersion.id == document_version_id,
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.status == DocumentVersionStatus.READY.value,
                DocumentIngestionGeneration.tenant_id == tenant_id,
                DocumentIngestionGeneration.document_version_id == document_version_id,
                DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                DocumentIngestionGeneration.active.is_(True),
            )
            .with_for_update()
        )
        if generation_id is None:
            raise AgentDocumentVersionNotReady()
        return generation_id

    @staticmethod
    async def _get_run(
        session: AsyncSession,
        *,
        run_id: UUID,
        tenant_id: UUID,
    ) -> AgentRun:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
        )
        if run is None:
            raise AgentRunNotFound()
        return run

    @staticmethod
    async def _status_result(
        session: AsyncSession,
        run: AgentRun,
    ) -> AgentRunStatusResult:
        executions = (
            await session.scalars(
                select(AgentRunExecution)
                .where(AgentRunExecution.run_id == run.id)
                .order_by(AgentRunExecution.sequence)
            )
        ).all()
        job_ids = [execution.job_id for execution in executions]
        jobs = (
            (await session.scalars(select(Job).where(Job.id.in_(job_ids)))).all() if job_ids else []
        )
        job_by_id = {job.id: job for job in jobs}
        attempts = (
            (
                await session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id.in_(job_ids))
                    .order_by(JobAttempt.job_id, JobAttempt.attempt_number)
                )
            ).all()
            if job_ids
            else []
        )
        attempts_by_job: dict[UUID, list[AgentRunAttemptResult]] = {}
        for attempt in attempts:
            attempts_by_job.setdefault(attempt.job_id, []).append(
                AgentRunAttemptResult(
                    attempt_id=attempt.id,
                    attempt_number=attempt.attempt_number,
                    status=attempt.status,
                    worker_id=attempt.worker_id,
                    started_at=attempt.started_at,
                    heartbeat_at=attempt.heartbeat_at,
                    finished_at=attempt.finished_at,
                    error_code=attempt.error_code,
                    diagnostic_code=attempt.diagnostic_code,
                )
            )
        execution_results: list[AgentRunExecutionResult] = []
        for execution in executions:
            job = job_by_id.get(execution.job_id)
            if job is None:
                raise AgentRunIntegrityError()
            execution_results.append(
                AgentRunExecutionResult(
                    execution_id=execution.id,
                    sequence=execution.sequence,
                    kind=execution.kind,
                    job_id=job.id,
                    job_status=job.status,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    cancel_requested=job.cancel_requested_at is not None,
                    attempt_history=tuple(attempts_by_job.get(job.id, ())),
                )
            )
        return AgentRunStatusResult(
            run_id=run.id,
            tenant_id=run.tenant_id,
            document_version_id=run.document_version_id,
            task_type=run.task_type,
            publish_requested=run.publish_requested,
            status=run.status,
            graph_version=run.graph_version,
            prompt_version=run.prompt_version,
            model_provider=run.model_provider,
            model_name=run.model_name,
            model_version=run.model_version,
            tool_schema_version=run.tool_schema_version,
            current_execution_seq=run.current_execution_seq,
            error_code=run.error_code,
            created_at=run.created_at,
            started_at=run.started_at,
            waiting_at=run.waiting_at,
            finished_at=run.finished_at,
            cancelled_at=run.cancelled_at,
            executions=tuple(execution_results),
            model_revision=run.model_revision,
            fallback_trigger_code=run.fallback_trigger_code,
            provider_request_count=run.provider_request_count,
            provider_usage_request_count=run.provider_usage_request_count,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            total_tokens=run.total_tokens,
            repair_request_count=run.repair_request_count,
            fallback_count=run.fallback_count,
            breaker_state=run.breaker_state,
        )
