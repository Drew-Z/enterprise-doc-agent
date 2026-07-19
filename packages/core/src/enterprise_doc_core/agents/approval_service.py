from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentRun,
    AgentRunExecution,
    AgentRunExecutionKind,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from enterprise_doc_core.agents.policy import (
    TargetResourceType,
    artifact_target_fingerprint,
)
from enterprise_doc_core.agents.service import append_agent_run_event
from enterprise_doc_core.agents.state import (
    AgentRunTransitionEvent,
    ApprovalRequestEvent,
    is_agent_run_terminal,
    transition_agent_run,
    transition_approval_request,
)
from enterprise_doc_core.documents.models import DocumentVersion, DocumentVersionStatus
from enterprise_doc_core.identity.models import (
    Membership,
    MembershipRole,
    Tenant,
    User,
)
from enterprise_doc_core.jobs import cancel_job_records, create_job_records
from enterprise_doc_core.telemetry import MetricsRuntime


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalError(Exception):
    code = "approval_error"
    message = "The approval request could not be completed."

    def __init__(self) -> None:
        super().__init__(self.message)


class ApprovalNotFound(ApprovalError):
    code = "approval_not_found"
    message = "The approval request was not found."


class ApprovalPrincipalForbidden(ApprovalError):
    code = "approval_principal_forbidden"
    message = "Only an active tenant owner can decide this approval."


class ApprovalInputInvalid(ApprovalError):
    code = "approval_input_invalid"
    message = "The approval decision input is invalid."


class ApprovalTargetMismatch(ApprovalError):
    code = "approval_target_mismatch"
    message = "The approval decision does not match the exact requested target."


class ApprovalTargetChanged(ApprovalError):
    code = "approval_target_changed"
    message = "The approval target is no longer eligible for this decision."


class ApprovalAlreadyDecided(ApprovalError):
    code = "approval_already_decided"
    message = "The approval request has already been decided."


class ApprovalRunNotWaiting(ApprovalError):
    code = "approval_run_not_waiting"
    message = "The Agent run is not waiting for this approval."


class ApprovalIntegrityError(ApprovalError):
    code = "approval_integrity_error"
    message = "The approval persistence state is incomplete."


@dataclass(frozen=True, slots=True)
class DecideApprovalInput:
    decision: ApprovalDecision | str
    operation: str
    target_resource_type: str
    target_resource_id: UUID
    target_document_version_id: UUID
    target_fingerprint: str
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    approval_id: UUID
    run_id: UUID
    status: str
    decision: str
    resume_job_id: UUID
    resume_execution_id: UUID
    decision_fingerprint: str
    replayed: bool
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalRequestResult:
    approval_id: UUID
    run_id: UUID
    status: str
    operation: str
    target_resource_type: str
    target_resource_id: UUID
    target_document_version_id: UUID
    target_fingerprint: str
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    can_decide: bool


@dataclass(frozen=True, slots=True)
class ApprovalRevocationResult:
    approval_id: UUID
    run_id: UUID
    status: str
    changed: bool
    resume_job_cancelled: bool


def _utcnow() -> datetime:
    return datetime.now(UTC)


def approval_decision_fingerprint(
    *,
    approval_id: UUID,
    request: DecideApprovalInput,
) -> str:
    decision = ApprovalDecision(request.decision)
    encoded = json.dumps(
        {
            "approval_id": str(approval_id),
            "comment": _normalize_comment(request.comment),
            "decision": decision.value,
            "operation": request.operation,
            "target_document_version_id": str(request.target_document_version_id),
            "target_fingerprint": request.target_fingerprint,
            "target_resource_id": str(request.target_resource_id),
            "target_resource_type": request.target_resource_type,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ApprovalService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utcnow,
        resume_max_attempts: int = 3,
        metrics: MetricsRuntime | None = None,
    ) -> None:
        if not 1 <= resume_max_attempts <= 100:
            raise ValueError("resume_max_attempts must be between 1 and 100")
        self.session_factory = session_factory
        self.clock = clock
        self.resume_max_attempts = resume_max_attempts
        self.metrics = metrics

    async def get(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequestResult:
        async with self.session_factory() as session:
            membership = await session.scalar(
                select(Membership)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == actor_id,
                    Membership.is_active.is_(True),
                    Tenant.is_active.is_(True),
                    User.is_active.is_(True),
                )
            )
            if membership is None:
                raise ApprovalNotFound()
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                )
            )
            if approval is None:
                raise ApprovalNotFound()
            return ApprovalRequestResult(
                approval_id=approval.id,
                run_id=approval.run_id,
                status=approval.status,
                operation=approval.operation,
                target_resource_type=approval.target_resource_type,
                target_resource_id=approval.target_resource_id,
                target_document_version_id=approval.target_document_version_id,
                target_fingerprint=approval.target_fingerprint,
                requested_at=approval.requested_at,
                expires_at=approval.expires_at,
                decided_at=approval.decided_at,
                can_decide=membership.role == MembershipRole.OWNER.value,
            )

    async def decide(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None,
        approval_id: UUID,
        idempotency_key: str,
        request: DecideApprovalInput,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ApprovalDecisionResult:
        started = perf_counter()
        result_label = "error"
        try:
            result = await self._decide(
                tenant_id=tenant_id,
                actor_id=actor_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                request=request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except ApprovalNotFound:
            result_label = "not_found"
            raise
        except ApprovalPrincipalForbidden:
            result_label = "forbidden"
            raise
        except ApprovalError:
            result_label = "permanent_error"
            raise
        except Exception:
            result_label = "error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="approval",
                    operation="decide",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def _decide(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None,
        approval_id: UUID,
        idempotency_key: str,
        request: DecideApprovalInput,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ApprovalDecisionResult:
        decision = self._validate_decision_input(
            idempotency_key=idempotency_key,
            request=request,
        )
        if decision is not ApprovalDecision.EXPIRED and actor_id is None:
            raise ApprovalPrincipalForbidden()
        fingerprint = approval_decision_fingerprint(
            approval_id=approval_id,
            request=request,
        )
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")

        async with self.session_factory.begin() as session:
            approval_run_id = await session.scalar(
                select(ApprovalRequest.run_id).where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                )
            )
            if approval_run_id is None:
                raise ApprovalNotFound()
            # All approval, cancellation, and publication mutations serialize on
            # the run before taking entity row locks. This prevents opposite
            # approval/run lock orders from deadlocking under concurrent actions.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"agent-run:{tenant_id}:{approval_run_id}"},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"approval:{tenant_id}:{approval_id}"},
            )
            if actor_id is not None:
                await self._require_owner(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == approval_run_id,
                    AgentRun.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if run is None:
                raise ApprovalIntegrityError()
            approval = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.run_id == run.id,
                )
                .with_for_update()
            )
            if approval is None:
                raise ApprovalNotFound()
            self._validate_exact_target(approval, request)

            existing_execution = await session.scalar(
                select(AgentRunExecution)
                .where(
                    AgentRunExecution.tenant_id == tenant_id,
                    AgentRunExecution.run_id == run.id,
                    AgentRunExecution.approval_request_id == approval.id,
                )
                .with_for_update()
            )
            if approval.status != ApprovalRequestStatus.PENDING.value:
                return self._existing_result(
                    approval=approval,
                    execution=existing_execution,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
            if run.status != AgentRunStatus.WAITING_APPROVAL.value:
                raise ApprovalRunNotWaiting()
            effective_decision = decision
            if approval.expires_at <= now:
                effective_decision = ApprovalDecision.EXPIRED
            if effective_decision is ApprovalDecision.APPROVED:
                await self._validate_current_target(
                    session,
                    tenant_id=tenant_id,
                    approval=approval,
                )

            approval.status = transition_approval_request(
                ApprovalRequestStatus.PENDING,
                {
                    ApprovalDecision.APPROVED: ApprovalRequestEvent.APPROVE,
                    ApprovalDecision.REJECTED: ApprovalRequestEvent.REJECT,
                    ApprovalDecision.EXPIRED: ApprovalRequestEvent.EXPIRE,
                }[effective_decision],
            ).value
            approval.decided_by_actor_id = actor_id
            approval.decision_idempotency_key = idempotency_key
            approval.decision_comment = _normalize_comment(request.comment)
            approval.decided_at = now

            sequence = run.current_execution_seq + 1
            job_result = await create_job_records(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id or run.actor_id,
                job_type="agent.execute",
                idempotency_key=f"agent:{run.id}:execution:{sequence}",
                payload={
                    "payload_version": 1,
                    "run_id": str(run.id),
                    "execution_sequence": sequence,
                    "graph_thread_id": run.graph_thread_id,
                    "graph_version": run.graph_version,
                },
                document_version_id=None,
                max_attempts=self.resume_max_attempts,
                request_id=request_id,
                correlation_id=correlation_id,
                available_at=now,
                outbox_event_type="agent.execute.requested",
            )
            if job_result.replayed:
                # A resume Job and its execution are created atomically below;
                # seeing an existing Job here means the persistence invariant
                # was already violated by an external writer.
                raise ApprovalIntegrityError()
            execution = AgentRunExecution(
                tenant_id=tenant_id,
                run_id=run.id,
                sequence=sequence,
                job_id=job_result.job_id,
                kind=AgentRunExecutionKind.RESUME.value,
                approval_request_id=approval.id,
                resume_fingerprint=fingerprint,
            )
            session.add(execution)
            run.current_execution_seq = sequence
            await session.flush()
            assert approval.decided_at is not None
            return ApprovalDecisionResult(
                approval_id=approval.id,
                run_id=run.id,
                status=approval.status,
                decision=effective_decision.value,
                resume_job_id=job_result.job_id,
                resume_execution_id=execution.id,
                decision_fingerprint=fingerprint,
                replayed=False,
                decided_at=approval.decided_at,
            )

    async def revoke(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRevocationResult:
        started = perf_counter()
        result_label = "error"
        try:
            result = await self._revoke(
                tenant_id=tenant_id,
                actor_id=actor_id,
                approval_id=approval_id,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except ApprovalNotFound:
            result_label = "not_found"
            raise
        except ApprovalPrincipalForbidden:
            result_label = "forbidden"
            raise
        except ApprovalError:
            result_label = "permanent_error"
            raise
        except Exception:
            result_label = "error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="approval",
                    operation="revoke",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def _revoke(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRevocationResult:
        now = self.clock()
        async with self.session_factory.begin() as session:
            approval_run_id = await session.scalar(
                select(ApprovalRequest.run_id).where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                )
            )
            if approval_run_id is None:
                raise ApprovalNotFound()
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"agent-run:{tenant_id}:{approval_run_id}"},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"approval:{tenant_id}:{approval_id}"},
            )
            await self._require_owner(session, tenant_id=tenant_id, actor_id=actor_id)
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.id == approval_run_id, AgentRun.tenant_id == tenant_id)
                .with_for_update()
            )
            if run is None:
                raise ApprovalIntegrityError()
            approval = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.run_id == run.id,
                )
                .with_for_update()
            )
            if approval is None:
                raise ApprovalNotFound()
            if approval.status == ApprovalRequestStatus.REVOKED.value:
                return ApprovalRevocationResult(
                    approval_id=approval.id,
                    run_id=approval.run_id,
                    status=approval.status,
                    changed=False,
                    resume_job_cancelled=False,
                )
            if approval.status not in {
                ApprovalRequestStatus.PENDING.value,
                ApprovalRequestStatus.APPROVED.value,
            }:
                raise ApprovalAlreadyDecided()
            approval.status = transition_approval_request(
                ApprovalRequestStatus(approval.status),
                ApprovalRequestEvent.REVOKE,
            ).value
            approval.revoked_at = now
            execution = await session.scalar(
                select(AgentRunExecution)
                .where(
                    AgentRunExecution.tenant_id == tenant_id,
                    AgentRunExecution.approval_request_id == approval.id,
                )
                .with_for_update()
            )
            cancelled = False
            if execution is not None:
                cancellation = await cancel_job_records(
                    session,
                    job_id=execution.job_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    now=now,
                )
                cancelled = cancellation.changed or cancellation.cancellation_requested
            current_status = AgentRunStatus(run.status)
            if not is_agent_run_terminal(current_status):
                run.status = transition_agent_run(
                    current_status,
                    AgentRunTransitionEvent.CANCEL,
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
            return ApprovalRevocationResult(
                approval_id=approval.id,
                run_id=approval.run_id,
                status=approval.status,
                changed=True,
                resume_job_cancelled=cancelled,
            )

    @staticmethod
    def _validate_decision_input(
        *,
        idempotency_key: str,
        request: DecideApprovalInput,
    ) -> ApprovalDecision:
        if not 1 <= len(idempotency_key) <= 128:
            raise ApprovalInputInvalid()
        try:
            decision = ApprovalDecision(request.decision)
        except ValueError as error:
            raise ApprovalInputInvalid() from error
        if (
            request.operation != "publish_artifact"
            or request.target_resource_type != TargetResourceType.ARTIFACT.value
            or len(request.target_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in request.target_fingerprint)
        ):
            raise ApprovalInputInvalid()
        _normalize_comment(request.comment)
        return decision

    @staticmethod
    async def _require_owner(
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
                Membership.role == MembershipRole.OWNER.value,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        if membership_id is None:
            raise ApprovalPrincipalForbidden()

    @staticmethod
    def _validate_exact_target(
        approval: ApprovalRequest,
        request: DecideApprovalInput,
    ) -> None:
        if (
            approval.operation != request.operation
            or approval.target_resource_type != request.target_resource_type
            or approval.target_resource_id != request.target_resource_id
            or approval.target_document_version_id != request.target_document_version_id
            or approval.target_fingerprint != request.target_fingerprint
        ):
            raise ApprovalTargetMismatch()

    @staticmethod
    async def _validate_current_target(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        approval: ApprovalRequest,
    ) -> None:
        version = await session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == approval.target_document_version_id,
                DocumentVersion.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        artifact = await session.scalar(
            select(AgentArtifact)
            .where(
                AgentArtifact.id == approval.target_resource_id,
                AgentArtifact.tenant_id == tenant_id,
                AgentArtifact.run_id == approval.run_id,
                AgentArtifact.source_document_version_id == approval.target_document_version_id,
            )
            .with_for_update()
        )
        if (
            version is None
            or artifact is None
            or artifact_target_fingerprint(artifact) != approval.target_fingerprint
            or version.status != DocumentVersionStatus.READY.value
        ):
            raise ApprovalTargetChanged()

    @staticmethod
    def _existing_result(
        *,
        approval: ApprovalRequest,
        execution: AgentRunExecution | None,
        idempotency_key: str,
        fingerprint: str,
    ) -> ApprovalDecisionResult:
        if (
            execution is None
            or approval.decision_idempotency_key != idempotency_key
            or execution.resume_fingerprint != fingerprint
            or approval.decided_at is None
        ):
            raise ApprovalAlreadyDecided()
        decision = {
            ApprovalRequestStatus.APPROVED.value: ApprovalDecision.APPROVED.value,
            ApprovalRequestStatus.REJECTED.value: ApprovalDecision.REJECTED.value,
            ApprovalRequestStatus.EXPIRED.value: ApprovalDecision.EXPIRED.value,
            # Publication consumes the approval, but an idempotent replay must
            # still report the decision the caller originally made.
            ApprovalRequestStatus.CONSUMED.value: ApprovalDecision.APPROVED.value,
        }.get(approval.status)
        if decision is None:
            raise ApprovalAlreadyDecided()
        return ApprovalDecisionResult(
            approval_id=approval.id,
            run_id=approval.run_id,
            status=approval.status,
            decision=decision,
            resume_job_id=execution.job_id,
            resume_execution_id=execution.id,
            decision_fingerprint=fingerprint,
            replayed=True,
            decided_at=approval.decided_at,
        )


def _normalize_comment(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > 1000:
        raise ApprovalInputInvalid()
    return normalized or None


__all__ = [
    "ApprovalAlreadyDecided",
    "ApprovalDecision",
    "ApprovalDecisionResult",
    "ApprovalError",
    "ApprovalInputInvalid",
    "ApprovalIntegrityError",
    "ApprovalNotFound",
    "ApprovalPrincipalForbidden",
    "ApprovalRevocationResult",
    "ApprovalRunNotWaiting",
    "ApprovalService",
    "ApprovalTargetChanged",
    "ApprovalTargetMismatch",
    "DecideApprovalInput",
    "approval_decision_fingerprint",
]
