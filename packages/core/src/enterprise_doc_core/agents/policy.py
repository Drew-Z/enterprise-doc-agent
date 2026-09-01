from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.agents.execution_context import SignedExecutionContext, ToolCapability
from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentRun,
    AgentRunEvidence,
    AgentRunExecution,
    AgentRunExecutionKind,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.policy import document_visible_to_actor
from enterprise_doc_core.identity.models import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.jobs.models import Job, JobAttempt, JobAttemptStatus, JobStatus


class ToolPolicyError(ValueError):
    """A deliberately non-enumerating tool authorization denial."""

    code = "tool_policy_denied"

    def __init__(self, detail: str = "tool policy denied") -> None:
        super().__init__(detail)


class ToolCapabilityError(ToolPolicyError):
    code = "tool_capability_denied"


class ToolPolicyNotFound(ToolPolicyError):
    code = "tool_policy_denied"


class ToolApprovalError(ToolPolicyError):
    code = "tool_approval_denied"


class TargetResourceType(StrEnum):
    ARTIFACT = "agent_artifact"


@dataclass(frozen=True, slots=True)
class AuthorizedToolScope:
    context: SignedExecutionContext
    membership_role: MembershipRole
    run: AgentRun
    execution: AgentRunExecution
    document_version: DocumentVersion
    evidence: AgentRunEvidence | None = None
    chunk: DocumentChunk | None = None
    artifact: AgentArtifact | None = None
    approval: ApprovalRequest | None = None


def artifact_target_fingerprint(artifact: AgentArtifact) -> str:
    """Fingerprint only immutable publication target fields, never the object key."""
    encoded = json.dumps(
        {
            "artifact_id": str(artifact.id),
            "content_sha256": artifact.content_sha256,
            "content_type": artifact.content_type,
            "kind": artifact.kind,
            "run_id": str(artifact.run_id),
            "size_bytes": artifact.size_bytes,
            "source_document_version_id": str(artifact.source_document_version_id),
            "tenant_id": str(artifact.tenant_id),
            "behavior_versions": artifact.behavior_versions,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def reload_tool_policy(
    session: AsyncSession,
    *,
    context: SignedExecutionContext,
    capability: ToolCapability,
    chunk_id: UUID | None = None,
    artifact_id: UUID | None = None,
    target_fingerprint: str | None = None,
    now: datetime | None = None,
    for_update: bool = False,
    allow_succeeded_publish_replay: bool = False,
) -> AuthorizedToolScope:
    """Reload all authorization inputs from PostgreSQL for one tool call."""
    if not context.allows(capability):
        raise ToolCapabilityError()
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    membership = await session.scalar(
        select(Membership)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.tenant_id == context.tenant_id,
            Membership.user_id == context.actor_id,
            Membership.is_active.is_(True),
            Tenant.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if membership is None:
        raise ToolPolicyNotFound()
    try:
        membership_role = MembershipRole(membership.role)
    except ValueError as error:
        raise ToolPolicyNotFound() from error

    run_statement = select(AgentRun).where(
        AgentRun.id == context.run_id,
        AgentRun.tenant_id == context.tenant_id,
        AgentRun.actor_id == context.actor_id,
        AgentRun.document_version_id == context.target_document_version_id,
    )
    if for_update:
        run_statement = run_statement.with_for_update()
    run = await session.scalar(run_statement)
    if run is None or run.status not in _allowed_run_statuses(
        capability,
        allow_succeeded_publish_replay=allow_succeeded_publish_replay,
    ):
        raise ToolPolicyNotFound()

    execution_statement = select(AgentRunExecution).where(
        AgentRunExecution.id == context.execution_id,
        AgentRunExecution.tenant_id == context.tenant_id,
        AgentRunExecution.run_id == run.id,
        AgentRunExecution.sequence == run.current_execution_seq,
    )
    if for_update:
        execution_statement = execution_statement.with_for_update()
    execution = await session.scalar(execution_statement)
    if execution is None:
        raise ToolPolicyNotFound()
    if context.job_id is not None:
        if (
            context.attempt_id is None
            or context.lease_token is None
            or context.fencing_token is None
            or execution.job_id != context.job_id
        ):
            raise ToolPolicyNotFound()
        job = await session.scalar(
            select(Job).where(
                Job.id == context.job_id,
                Job.tenant_id == context.tenant_id,
                Job.status == JobStatus.RUNNING.value,
                Job.lease_token == context.lease_token,
                Job.fencing_token == context.fencing_token,
            )
        )
        attempt = await session.scalar(
            select(JobAttempt).where(
                JobAttempt.id == context.attempt_id,
                JobAttempt.tenant_id == context.tenant_id,
                JobAttempt.job_id == context.job_id,
                JobAttempt.status == JobAttemptStatus.RUNNING.value,
                JobAttempt.lease_token == context.lease_token,
                JobAttempt.fencing_token == context.fencing_token,
            )
        )
        if job is None or attempt is None:
            raise ToolPolicyNotFound()
    if capability is ToolCapability.PUBLISH and (
        execution.kind != AgentRunExecutionKind.RESUME.value
        or context.approval_request_id is None
        or execution.approval_request_id != context.approval_request_id
    ):
        raise ToolApprovalError()

    version = await session.scalar(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.id == context.target_document_version_id,
            DocumentVersion.tenant_id == context.tenant_id,
            DocumentVersion.status == DocumentVersionStatus.READY.value,
            document_visible_to_actor(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
            ),
        )
    )
    if version is None:
        raise ToolPolicyNotFound()
    generation_id = run.index_generation_id
    if generation_id is None:
        raise ToolPolicyNotFound()
    generation = await session.scalar(
        select(DocumentIngestionGeneration).where(
            DocumentIngestionGeneration.id == generation_id,
            DocumentIngestionGeneration.tenant_id == context.tenant_id,
            DocumentIngestionGeneration.document_version_id == version.id,
            DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
            DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
            DocumentIngestionGeneration.active.is_(True),
        )
    )
    if generation is None:
        raise ToolPolicyNotFound()

    evidence: AgentRunEvidence | None = None
    chunk: DocumentChunk | None = None
    if capability is ToolCapability.READ_EVIDENCE and chunk_id is not None:
        evidence, chunk = (
            await session.execute(
                select(AgentRunEvidence, DocumentChunk)
                .join(DocumentChunk, DocumentChunk.id == AgentRunEvidence.chunk_id)
                .join(
                    DocumentIngestionGeneration,
                    DocumentIngestionGeneration.id == DocumentChunk.generation_id,
                )
                .where(
                    AgentRunEvidence.tenant_id == context.tenant_id,
                    AgentRunEvidence.run_id == run.id,
                    AgentRunEvidence.chunk_id == chunk_id,
                    AgentRunEvidence.document_version_id == version.id,
                    AgentRunEvidence.generation_id == generation_id,
                    DocumentChunk.tenant_id == context.tenant_id,
                    DocumentChunk.document_version_id == version.id,
                    DocumentChunk.generation_id == generation_id,
                    DocumentIngestionGeneration.tenant_id == context.tenant_id,
                    DocumentIngestionGeneration.document_version_id == version.id,
                    DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                    DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                    DocumentIngestionGeneration.active.is_(True),
                )
            )
        ).one_or_none() or (None, None)
        if evidence is None or chunk is None:
            raise ToolPolicyNotFound()

    artifact: AgentArtifact | None = None
    approval: ApprovalRequest | None = None
    if capability in {
        ToolCapability.CREATE_DRAFT,
        ToolCapability.READ_ARTIFACT,
        ToolCapability.PUBLISH,
    }:
        if artifact_id is not None:
            artifact = await session.scalar(
                select(AgentArtifact).where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == context.tenant_id,
                    AgentArtifact.run_id == run.id,
                    AgentArtifact.source_document_version_id == version.id,
                )
            )
        if capability is ToolCapability.PUBLISH and artifact is None:
            raise ToolPolicyNotFound()
        if capability is ToolCapability.READ_ARTIFACT and artifact is None:
            raise ToolPolicyNotFound()
        if capability is ToolCapability.READ_ARTIFACT and artifact is not None:
            if artifact.status not in {
                AgentArtifactStatus.DRAFT_READY.value,
                AgentArtifactStatus.PUBLISHED.value,
            }:
                raise ToolPolicyNotFound()
        if capability is ToolCapability.PUBLISH:
            assert artifact is not None
            if not run.publish_requested:
                raise ToolApprovalError()
            if membership_role is not MembershipRole.OWNER:
                raise ToolApprovalError()
            if artifact.status not in {
                AgentArtifactStatus.DRAFT_READY.value,
                AgentArtifactStatus.PUBLISHED.value,
            }:
                raise ToolApprovalError()
            if context.approval_request_id is None or target_fingerprint is None:
                raise ToolApprovalError()
            approval_result = await session.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == context.approval_request_id,
                    ApprovalRequest.tenant_id == context.tenant_id,
                    ApprovalRequest.run_id == run.id,
                    ApprovalRequest.operation == "publish_artifact",
                    ApprovalRequest.target_resource_type == TargetResourceType.ARTIFACT.value,
                    ApprovalRequest.target_resource_id == artifact.id,
                    ApprovalRequest.target_document_version_id == version.id,
                )
                .with_for_update()
            )
            approval = approval_result.scalar_one_or_none()
            allowed_approval_status = approval is not None and (
                (
                    artifact.status == AgentArtifactStatus.DRAFT_READY.value
                    and approval.status == ApprovalRequestStatus.APPROVED.value
                    and approval.expires_at > current
                )
                or (
                    artifact.status == AgentArtifactStatus.PUBLISHED.value
                    and approval.status == ApprovalRequestStatus.CONSUMED.value
                )
            )
            if not allowed_approval_status:
                raise ToolApprovalError()
            assert approval is not None
            if approval.target_fingerprint != target_fingerprint or (
                artifact_target_fingerprint(artifact) != target_fingerprint
            ):
                raise ToolApprovalError()

    return AuthorizedToolScope(
        context=context,
        membership_role=membership_role,
        run=run,
        execution=execution,
        document_version=version,
        evidence=evidence,
        chunk=chunk,
        artifact=artifact,
        approval=approval,
    )


def _allowed_run_statuses(
    capability: ToolCapability,
    *,
    allow_succeeded_publish_replay: bool = False,
) -> set[str]:
    if capability in {ToolCapability.READ_EVIDENCE, ToolCapability.CREATE_DRAFT}:
        return {AgentRunStatus.RUNNING.value}
    if capability is ToolCapability.PUBLISH:
        statuses = {AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING_APPROVAL.value}
        if allow_succeeded_publish_replay:
            statuses.add(AgentRunStatus.SUCCEEDED.value)
        return statuses
    return {
        AgentRunStatus.RUNNING.value,
        AgentRunStatus.WAITING_APPROVAL.value,
        AgentRunStatus.SUCCEEDED.value,
    }


__all__ = [
    "AuthorizedToolScope",
    "TargetResourceType",
    "ToolApprovalError",
    "ToolCapabilityError",
    "ToolPolicyError",
    "ToolPolicyNotFound",
    "artifact_target_fingerprint",
    "reload_tool_policy",
]
