from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentRunTaskType(StrEnum):
    QUESTION_ANSWER = "question_answer"
    SUMMARY = "summary"
    STRUCTURED_EXTRACTION = "structured_extraction"


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AgentRunExecutionKind(StrEnum):
    INITIAL = "initial"
    RESUME = "resume"


class ApprovalRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class AgentArtifactStatus(StrEnum):
    WRITING = "writing"
    DRAFT_READY = "draft_ready"
    PUBLISHED = "published"
    FAILED = "failed"
    REVOKED = "revoked"


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_agent_runs_tenant_id_idempotency_key",
        ),
        UniqueConstraint("graph_thread_id", name="uq_agent_runs_graph_thread_id"),
        CheckConstraint(
            "task_type IN ('question_answer', 'summary', 'structured_extraction')",
            name="task_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', "
            "'refused', 'failed', 'cancelled', 'rejected', 'expired')",
            name="status_valid",
        ),
        CheckConstraint("next_event_seq > 0", name="next_event_seq_positive"),
        CheckConstraint(
            "current_execution_seq >= 0",
            name="current_execution_seq_non_negative",
        ),
        Index(
            "ix_agent_runs_tenant_id_status_created_at",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_agent_runs_tenant_id_document_version_id_created_at",
            "tenant_id",
            "document_version_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    publish_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=AgentRunStatus.PENDING.value
    )
    graph_thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    index_generation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_ingestion_generations.id", ondelete="CASCADE"),
        nullable=True,
    )
    next_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    current_execution_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waiting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRunExecution(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_run_executions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_run_executions_run_id_sequence",
        ),
        UniqueConstraint("job_id", name="uq_agent_run_executions_job_id"),
        UniqueConstraint(
            "approval_request_id",
            name="uq_agent_run_executions_approval_request_id",
        ),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint("kind IN ('initial', 'resume')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'initial' AND sequence = 0 AND approval_request_id IS NULL "
            "AND resume_fingerprint IS NULL) OR "
            "(kind = 'resume' AND sequence > 0 AND approval_request_id IS NOT NULL "
            "AND resume_fingerprint IS NOT NULL)",
            name="resume_shape_valid",
        ),
        Index(
            "ix_agent_run_executions_tenant_id_run_id_sequence",
            "tenant_id",
            "run_id",
            "sequence",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=True
    )
    resume_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRunEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_agent_run_events_run_id_seq"),
        CheckConstraint("seq > 0", name="seq_positive"),
        CheckConstraint("event_version > 0", name="event_version_positive"),
        Index("ix_agent_run_events_tenant_id_run_id_seq", "tenant_id", "run_id", "seq"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    public_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRunEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_run_evidence"
    __table_args__ = (
        UniqueConstraint("run_id", "chunk_id", name="uq_agent_run_evidence_run_id_chunk_id"),
        UniqueConstraint("run_id", "rank", name="uq_agent_run_evidence_run_id_rank"),
        CheckConstraint("rank > 0", name="rank_positive"),
        CheckConstraint("rrf_score > 0", name="rrf_score_positive"),
        Index("ix_agent_run_evidence_tenant_id_run_id_rank", "tenant_id", "run_id", "rank"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_ingestion_generations.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    rrf_score: Mapped[float] = mapped_column(Float, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalRequest(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "decision_idempotency_key",
            name="uq_approval_requests_tenant_id_decision_idempotency_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "operation",
            "target_resource_type",
            "target_resource_id",
            "target_document_version_id",
            "target_fingerprint",
            name="uq_approval_requests_exact_target",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'revoked', 'consumed')",
            name="status_valid",
        ),
        CheckConstraint("operation IN ('publish_artifact')", name="operation_valid"),
        CheckConstraint("expires_at > requested_at", name="expiry_after_request"),
        Index(
            "uq_approval_requests_pending_run",
            "tenant_id",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_approval_requests_tenant_id_run_id_status",
            "tenant_id",
            "run_id",
            "status",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    target_resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_resource_id: Mapped[UUID] = mapped_column(nullable=False)
    target_document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    target_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ApprovalRequestStatus.PENDING.value
    )
    decision_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolExecution(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_tool_executions_tenant_id_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'denied')",
            name="status_valid",
        ),
        CheckConstraint(
            "(target_resource_type IS NULL AND target_resource_id IS NULL) OR "
            "(target_resource_type IS NOT NULL AND target_resource_id IS NOT NULL)",
            name="target_pair_valid",
        ),
        Index(
            "ix_tool_executions_tenant_id_run_id_created_at",
            "tenant_id",
            "run_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    target_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ToolExecutionStatus.PENDING.value
    )
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", name="uq_agent_artifacts_run_id_kind"),
        UniqueConstraint(
            "tenant_id",
            "object_bucket",
            "object_key",
            name="uq_agent_artifacts_object_location",
        ),
        CheckConstraint(
            "status IN ('writing', 'draft_ready', 'published', 'failed', 'revoked')",
            name="status_valid",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="size_bytes_non_negative",
        ),
        CheckConstraint(
            "(content_sha256 IS NULL AND size_bytes IS NULL) OR "
            "(content_sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name="content_metadata_pair",
        ),
        Index(
            "ix_agent_artifacts_tenant_id_run_id_status",
            "tenant_id",
            "run_id",
            "status",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=AgentArtifactStatus.WRITING.value
    )
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    behavior_versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
