from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents import (
    AgentRun,
    AgentRunExecution,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    GroundingValidationError,
    ModelCallTelemetry,
    ModelGatewayError,
    ModelIdentity,
    append_agent_run_event,
)
from enterprise_doc_core.agents.models import AgentRunExecutionKind
from enterprise_doc_core.agents.state import (
    AgentRunTransitionEvent,
    is_agent_run_terminal,
    transition_agent_run,
)
from enterprise_doc_core.jobs import ClaimedJob, JobStatus
from enterprise_doc_core.jobs.models import JobAttempt
from enterprise_doc_worker.queue import JobHandlerError

AGENT_EXECUTE_JOB_TYPE = "agent.execute"
_LOGGER = logging.getLogger("enterprise_doc_worker.agent_handler")


class AgentExecutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    payload_version: int
    run_id: str
    execution_sequence: int = Field(ge=0)
    graph_thread_id: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=64)

    @field_validator("payload_version")
    @classmethod
    def validate_payload_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported agent execution payload version")
        return value

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as error:
            raise ValueError("run_id must be a UUID string") from error

    @field_validator("graph_thread_id", "graph_version")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent execution strings must not be blank")
        return value

    @model_validator(mode="after")
    def validate_thread_binding(self) -> AgentExecutionPayload:
        if self.graph_thread_id != self.run_id:
            raise ValueError("graph thread must be bound to the run")
        return self

    @property
    def run_uuid(self) -> UUID:
        return UUID(self.run_id)


class AgentExecutionPayloadInvalid(JobHandlerError):
    code = "agent_execution_payload_invalid"
    message = "The Agent execution payload is invalid."


class AgentExecutionContractMismatch(JobHandlerError):
    code = "agent_execution_contract_mismatch"
    message = "The Agent execution claim no longer matches the current run."


class AgentExecutionStaleRun(JobHandlerError):
    """A retried claim belongs to a run that already reached a terminal state."""

    code = "agent_execution_stale_run"
    message = "The Agent run is already terminal; the stale execution is cancelled."


class AgentExecutionRuntimeError(JobHandlerError):
    code = "agent_execution_failed"
    message = "The Agent execution failed."

    def __init__(
        self,
        *,
        code: str | None = None,
        message: str | None = None,
        retryable: bool = False,
        diagnostic_code: str | None = None,
        failure_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code or type(self).code
        self.message = message or type(self).message
        self.retryable = retryable
        super().__init__(
            self.message,
            diagnostic_code=diagnostic_code,
            failure_metadata=failure_metadata,
        )


class _ModelFailureProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    identity: ModelIdentity
    telemetry: ModelCallTelemetry


def _model_failure_metadata(error: Exception) -> dict[str, Any] | None:
    if not isinstance(error, ModelGatewayError) or error.identity is None:
        return None
    projection = _ModelFailureProjection(identity=error.identity, telemetry=error.telemetry)
    return {"model_failure": projection.model_dump(mode="json")}


def _model_failure_projection(
    failure_metadata: Mapping[str, Any] | None,
) -> _ModelFailureProjection | None:
    if failure_metadata is None:
        return None
    try:
        return _ModelFailureProjection.model_validate(failure_metadata.get("model_failure"))
    except ValidationError:
        return None


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    tenant_id: UUID
    actor_id: UUID
    run_id: UUID
    execution_id: UUID
    execution_sequence: int
    execution_kind: str
    job_id: UUID
    attempt_id: UUID
    attempt_number: int
    worker_id: str
    lease_token: UUID
    fencing_token: int
    document_version_id: UUID
    task_type: str
    publish_requested: bool
    graph_thread_id: str
    graph_version: str
    run_status: str
    approval_request_id: UUID | None
    approval_decision: str | None = None
    approval_decision_fingerprint: str | None = None


class AgentExecutionLoader(Protocol):
    async def load(
        self,
        claim: ClaimedJob,
        payload: AgentExecutionPayload,
    ) -> AgentExecutionContext: ...


class AgentExecutionExecutor(Protocol):
    async def __call__(self, context: AgentExecutionContext) -> None: ...


class SqlAlchemyAgentExecutionLoader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def load(
        self,
        claim: ClaimedJob,
        payload: AgentExecutionPayload,
    ) -> AgentExecutionContext:
        async with self.session_factory() as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == payload.run_uuid))
            if run is None:
                raise AgentExecutionContractMismatch()
            execution = await session.scalar(
                select(AgentRunExecution).where(
                    AgentRunExecution.run_id == run.id,
                    AgentRunExecution.sequence == payload.execution_sequence,
                )
            )
            approval = (
                await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id == execution.approval_request_id,
                        ApprovalRequest.run_id == run.id,
                    )
                )
                if execution is not None and execution.approval_request_id is not None
                else None
            )

        if execution is None or not _claim_matches_run(
            claim,
            payload,
            run,
            execution,
            approval,
        ):
            if (
                execution is not None
                and claim.tenant_id == run.tenant_id
                and claim.actor_id == run.actor_id
                and payload.run_uuid == run.id
                and payload.execution_sequence == run.current_execution_seq
                and payload.graph_thread_id == run.graph_thread_id
                and payload.graph_version == run.graph_version
                and execution.tenant_id == run.tenant_id
                and execution.run_id == run.id
                and execution.sequence == payload.execution_sequence
                and execution.job_id == claim.job_id
                and is_agent_run_terminal(AgentRunStatus(run.status))
            ):
                raise AgentExecutionStaleRun()
            raise AgentExecutionContractMismatch()

        return AgentExecutionContext(
            tenant_id=run.tenant_id,
            actor_id=run.actor_id,
            run_id=run.id,
            execution_id=execution.id,
            execution_sequence=execution.sequence,
            execution_kind=execution.kind,
            job_id=execution.job_id,
            attempt_id=claim.attempt_id,
            attempt_number=claim.attempt_number,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            fencing_token=claim.fencing_token,
            document_version_id=run.document_version_id,
            task_type=run.task_type,
            publish_requested=run.publish_requested,
            graph_thread_id=run.graph_thread_id,
            graph_version=run.graph_version,
            run_status=run.status,
            approval_request_id=execution.approval_request_id,
            approval_decision=approval.status if approval is not None else None,
            approval_decision_fingerprint=execution.resume_fingerprint,
        )


def _claim_matches_run(
    claim: ClaimedJob,
    payload: AgentExecutionPayload,
    run: AgentRun,
    execution: AgentRunExecution,
    approval: ApprovalRequest | None,
) -> bool:
    if (
        claim.job_type != AGENT_EXECUTE_JOB_TYPE
        or claim.tenant_id != run.tenant_id
        or claim.actor_id != run.actor_id
        or payload.run_uuid != run.id
        or payload.execution_sequence != run.current_execution_seq
        or payload.graph_thread_id != run.graph_thread_id
        or payload.graph_version != run.graph_version
        or execution.tenant_id != run.tenant_id
        or execution.run_id != run.id
        or execution.sequence != payload.execution_sequence
        or execution.job_id != claim.job_id
    ):
        return False

    if execution.kind == AgentRunExecutionKind.INITIAL.value:
        return (
            execution.sequence == 0
            and execution.approval_request_id is None
            and run.status
            in {
                AgentRunStatus.PENDING.value,
                AgentRunStatus.RUNNING.value,
                AgentRunStatus.WAITING_APPROVAL.value,
            }
        )
    if execution.kind == AgentRunExecutionKind.RESUME.value:
        return (
            execution.sequence > 0
            and execution.approval_request_id is not None
            and execution.resume_fingerprint is not None
            and approval is not None
            and approval.tenant_id == run.tenant_id
            and approval.run_id == run.id
            and approval.status
            in {
                ApprovalRequestStatus.APPROVED.value,
                ApprovalRequestStatus.REJECTED.value,
                ApprovalRequestStatus.EXPIRED.value,
            }
            and run.status in {AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.RUNNING.value}
        )
    return False


def agent_failure_lock_key(claim: ClaimedJob) -> str | None:
    """Serialize terminal Agent Job failures with run mutations."""
    if claim.job_type != AGENT_EXECUTE_JOB_TYPE:
        return None
    try:
        payload = AgentExecutionPayload.model_validate(claim.payload)
    except ValidationError:
        return None
    return f"agent-run:{claim.tenant_id}:{payload.run_id}"


async def project_agent_run_failure(
    session: AsyncSession,
    *,
    claim: ClaimedJob,
    status: str,
    error_code: str,
    error_message: str,
    failure_metadata: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    """Project a terminal Agent Job failure onto its active run exactly once."""
    if claim.job_type != AGENT_EXECUTE_JOB_TYPE or status != JobStatus.DEAD.value:
        return
    try:
        payload = AgentExecutionPayload.model_validate(claim.payload)
    except ValidationError:
        return

    attempt = await session.scalar(
        select(JobAttempt).where(
            JobAttempt.id == claim.attempt_id,
            JobAttempt.tenant_id == claim.tenant_id,
            JobAttempt.job_id == claim.job_id,
            JobAttempt.attempt_number == claim.attempt_number,
            JobAttempt.worker_id == claim.worker_id,
            JobAttempt.lease_token == claim.lease_token,
            JobAttempt.fencing_token == claim.fencing_token,
        )
    )
    execution = await session.scalar(
        select(AgentRunExecution).where(
            AgentRunExecution.tenant_id == claim.tenant_id,
            AgentRunExecution.run_id == payload.run_uuid,
            AgentRunExecution.sequence == payload.execution_sequence,
            AgentRunExecution.job_id == claim.job_id,
        )
    )
    if attempt is None or execution is None:
        return

    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == payload.run_uuid,
            AgentRun.tenant_id == claim.tenant_id,
            AgentRun.actor_id == claim.actor_id,
            AgentRun.current_execution_seq == payload.execution_sequence,
            AgentRun.graph_thread_id == payload.graph_thread_id,
            AgentRun.graph_version == payload.graph_version,
        )
        .with_for_update()
    )
    if run is None or is_agent_run_terminal(AgentRunStatus(run.status)):
        return

    model_failure = _model_failure_projection(failure_metadata)
    if model_failure is not None:
        run.model_provider = model_failure.identity.provider
        run.model_name = model_failure.identity.model_name
        run.model_version = model_failure.identity.model_version
        run.model_revision = model_failure.identity.model_revision
        run.fallback_trigger_code = model_failure.telemetry.fallback_trigger_code
        run.provider_request_count = model_failure.telemetry.provider_request_count
        run.provider_usage_request_count = model_failure.telemetry.usage_request_count
        run.prompt_tokens = model_failure.telemetry.prompt_tokens
        run.completion_tokens = model_failure.telemetry.completion_tokens
        run.total_tokens = model_failure.telemetry.total_tokens
        run.repair_request_count = model_failure.telemetry.repair_request_count
        run.fallback_count = model_failure.telemetry.fallback_count
        run.breaker_state = model_failure.telemetry.breaker_state

    run.status = transition_agent_run(
        AgentRunStatus(run.status),
        AgentRunTransitionEvent.FAIL,
    ).value
    run.error_code = error_code[:100]
    run.error_message = error_message[:1000]
    run.finished_at = now
    await append_agent_run_event(
        session,
        tenant_id=run.tenant_id,
        run_id=run.id,
        event_type="run.finished",
        payload={"status": AgentRunStatus.FAILED.value, "refusal_reason": None},
        actor_id=run.actor_id,
    )


class AgentExecutionHandler:
    def __init__(
        self,
        *,
        loader: AgentExecutionLoader,
        executor: AgentExecutionExecutor,
    ) -> None:
        self.loader = loader
        self.executor = executor

    async def __call__(self, claim: ClaimedJob) -> None:
        if claim.job_type != AGENT_EXECUTE_JOB_TYPE:
            raise AgentExecutionContractMismatch()
        try:
            payload = AgentExecutionPayload.model_validate(claim.payload)
        except ValidationError as error:
            raise AgentExecutionPayloadInvalid() from error
        context = await self.loader.load(claim, payload)
        try:
            await self.executor(context)
        except JobHandlerError:
            raise
        except Exception as error:
            code = getattr(error, "code", None)
            retryable = bool(getattr(error, "retryable", False))
            diagnostic_code = (
                error.diagnostic_code if isinstance(error, GroundingValidationError) else None
            )
            _LOGGER.error(
                "agent_execution_handler_failed",
                extra={
                    "event_data": {
                        "run_id": str(context.run_id),
                        "execution_id": str(context.execution_id),
                        "execution_kind": context.execution_kind,
                        "error_type": type(error).__name__,
                        "error_code": code if isinstance(code, str) else None,
                        "cause_type": (
                            type(error.__cause__).__name__ if error.__cause__ is not None else None
                        ),
                        "exception_group_leaf_count": (
                            len(error.exceptions) if isinstance(error, ExceptionGroup) else None
                        ),
                    }
                },
                exc_info=True,
            )
            raise AgentExecutionRuntimeError(
                code=code,
                retryable=retryable,
                diagnostic_code=diagnostic_code,
                failure_metadata=_model_failure_metadata(error),
            ) from error


def build_agent_execution_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    executor: AgentExecutionExecutor,
) -> AgentExecutionHandler:
    return AgentExecutionHandler(
        loader=SqlAlchemyAgentExecutionLoader(session_factory),
        executor=executor,
    )


__all__ = [
    "AGENT_EXECUTE_JOB_TYPE",
    "AgentExecutionContext",
    "AgentExecutionContractMismatch",
    "AgentExecutionExecutor",
    "AgentExecutionHandler",
    "AgentExecutionLoader",
    "AgentExecutionPayload",
    "AgentExecutionPayloadInvalid",
    "AgentExecutionRuntimeError",
    "AgentExecutionStaleRun",
    "SqlAlchemyAgentExecutionLoader",
    "agent_failure_lock_key",
    "build_agent_execution_handler",
    "project_agent_run_failure",
]
