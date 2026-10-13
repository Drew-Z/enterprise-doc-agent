from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_run_scope"),
        UniqueConstraint("tenant_id", "project_id", "idempotency_key", name="uq_analysis_run_key"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["analysis_projects.tenant_id", "analysis_projects.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["analysis_source_snapshots.tenant_id", "analysis_source_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "base_run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="RESTRICT"
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_input',"
            "'completed','partial','failed','cancelled','expired')",
            name="status_valid",
        ),
        CheckConstraint("total_units BETWEEN 1 AND 30", name="unit_count_valid"),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    project_id: Mapped[UUID] = mapped_column(index=True)
    snapshot_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID | None] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128))
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy_hash: Mapped[str] = mapped_column(String(64))
    source_manifest_hash: Mapped[str] = mapped_column(String(64))
    base_run_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="queued")
    total_units: Mapped[int] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalysisUnit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_unit_scope"),
        UniqueConstraint("run_id", "object_id", "field_id", name="uq_analysis_unit_field"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reused_from_unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_input',"
            "'succeeded','failed','cancelled','expired')",
            name="status_valid",
        ),
        CheckConstraint("freshness IN ('current','stale')", name="freshness_valid"),
        CheckConstraint(
            "jsonb_typeof(invalidation_reasons) = 'array'",
            name="invalidation_reasons_array",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column(index=True)
    object_id: Mapped[str] = mapped_column(String(64))
    field_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    freshness: Mapped[str] = mapped_column(String(16), default="current", server_default="current")
    invalidation_reasons: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    reused_from_unit_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(80))
    evidence_set: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisDependency(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analysis_dependencies"
    __table_args__ = (
        UniqueConstraint("unit_id", "kind", "dependency_key", name="uq_analysis_dependency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN "
            "('object','policy','field','parameter','object_source','common_source',"
            "'upstream_cell')",
            name="kind_valid",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(24))
    dependency_key: Mapped[str] = mapped_column(String(160))
    fingerprint: Mapped[str] = mapped_column(String(64))


class AnalysisAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_actions"
    __table_args__ = (
        UniqueConstraint("unit_id", "slot", name="uq_analysis_action_slot"),
        UniqueConstraint("provider_dispatch_id", name="uq_analysis_dispatch_action"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id", "provider_dispatch_id"],
            [
                "provider_dispatches.tenant_id",
                "provider_dispatches.operation_id",
                "provider_dispatches.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("slot BETWEEN 1 AND 2", name="slot_valid"),
        CheckConstraint(
            "state IN ('dispatched','retryable','completed','failed','unknown')", name="state_valid"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column()
    slot: Mapped[int] = mapped_column(Integer)
    provider_dispatch_id: Mapped[UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="dispatched")
    error_code: Mapped[str | None] = mapped_column(String(80))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisCellRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_cell_revisions"
    __table_args__ = (
        UniqueConstraint("unit_id", "revision", name="uq_analysis_cell_revision"),
        UniqueConstraint("tenant_id", "id", name="uq_analysis_cell_revision_tenant"),
        UniqueConstraint("tenant_id", "unit_id", "id", name="uq_analysis_cell_revision_scope"),
        UniqueConstraint("unit_id", "idempotency_key", name="uq_analysis_cell_revision_key"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id", "supersedes_id"],
            [
                "analysis_cell_revisions.tenant_id",
                "analysis_cell_revisions.unit_id",
                "analysis_cell_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reused_from_revision_id"],
            ["analysis_cell_revisions.tenant_id", "analysis_cell_revisions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("author_type IN ('model','user')", name="author_type_valid"),
        CheckConstraint(
            "(idempotency_key IS NULL) = (request_fingerprint IS NULL)",
            name="idempotency_complete",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    author_type: Mapped[str] = mapped_column(String(16), default="model")
    actor_id: Mapped[UUID | None] = mapped_column()
    reason: Mapped[str] = mapped_column(String(2000), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    supersedes_id: Mapped[UUID | None] = mapped_column()
    reused_from_revision_id: Mapped[UUID | None] = mapped_column()


class AnalysisReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_reviews"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_review_scope"),
        UniqueConstraint("cell_revision_id", name="uq_analysis_review_revision"),
        UniqueConstraint("unit_id", "idempotency_key", name="uq_analysis_review_key"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id", "cell_revision_id"],
            [
                "analysis_cell_revisions.tenant_id",
                "analysis_cell_revisions.unit_id",
                "analysis_cell_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('approved','changes_requested','rejected')", name="decision_valid"
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column(index=True)
    cell_revision_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(2000))
    actor_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))


class AnalysisArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_artifacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_artifact_scope"),
        UniqueConstraint("run_id", "matrix_revision", name="uq_analysis_artifact_revision"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_analysis_artifact_key"),
        UniqueConstraint(
            "tenant_id", "object_bucket", "object_key", name="uq_analysis_artifact_location"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("mode IN ('draft','reviewed')", name="mode_valid"),
        CheckConstraint("status IN ('writing','ready','approved','failed')", name="status_valid"),
        CheckConstraint("format = 'xlsx'", name="format_valid"),
        CheckConstraint("matrix_revision > 0", name="matrix_revision_positive"),
        CheckConstraint(
            "(content_hash IS NULL AND size_bytes IS NULL) OR "
            "(content_hash IS NOT NULL AND size_bytes > 0)",
            name="content_metadata_pair",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column(index=True)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="writing")
    format: Mapped[str] = mapped_column(String(16), default="xlsx")
    matrix_revision: Mapped[int] = mapped_column(Integer)
    matrix_hash: Mapped[str] = mapped_column(String(64))
    matrix_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_manifest_hash: Mapped[str] = mapped_column(String(64))
    cell_revision_ids: Mapped[list[str]] = mapped_column(JSONB)
    object_bucket: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(512))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_approvals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_approval_scope"),
        UniqueConstraint("artifact_id", name="uq_analysis_approval_artifact"),
        UniqueConstraint("artifact_id", "idempotency_key", name="uq_analysis_approval_key"),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["analysis_artifacts.tenant_id", "analysis_artifacts.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("operation = 'deliver'", name="operation_valid"),
        CheckConstraint("decision IN ('approved','rejected')", name="decision_valid"),
        CheckConstraint("expires_at > decided_at", name="expiry_after_decision"),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    artifact_id: Mapped[UUID] = mapped_column(index=True)
    artifact_hash: Mapped[str] = mapped_column(String(64))
    source_manifest_hash: Mapped[str] = mapped_column(String(64))
    matrix_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(2000))
    actor_id: Mapped[UUID] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))


class AnalysisInputRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_input_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_input_request_scope"),
        UniqueConstraint("unit_id", "revision", name="uq_analysis_input_request_revision"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("state IN ('open','submitted','cancelled')", name="state_valid"),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column(index=True)
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="open")
    parameters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    values: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    submission_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_by: Mapped[UUID | None] = mapped_column()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisToolExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_tool_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_tool_execution_scope"),
        UniqueConstraint("unit_id", "ordinal", name="uq_analysis_tool_execution_ordinal"),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 3", name="ordinal_valid"),
        CheckConstraint(
            "state IN ('requested','waiting_input','completed','failed')", name="state_valid"
        ),
        CheckConstraint(
            "tool_name IN ('search_sources','read_source','calculate')", name="tool_name_valid"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    unit_id: Mapped[UUID] = mapped_column(index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(32))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="requested")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AnalysisEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_analysis_event_sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            ondelete="RESTRICT",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    unit_id: Mapped[UUID | None] = mapped_column()
    code: Mapped[str | None] = mapped_column(String(80))
