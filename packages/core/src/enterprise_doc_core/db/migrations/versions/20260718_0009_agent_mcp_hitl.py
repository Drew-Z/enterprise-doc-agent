"""Add Agent runs, approval, tool, evidence, and artifact persistence.

Revision ID: 20260718_0009
Revises: 20260718_0008
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260718_0009"
down_revision = "20260718_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M4_TABLES = (
    "agent_run_executions",
    "agent_run_events",
    "agent_run_evidence",
    "approval_requests",
    "tool_executions",
    "agent_artifacts",
    "agent_runs",
)


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("extraction_schema", sa.JSON(), nullable=True),
        sa.Column(
            "publish_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("graph_thread_id", sa.String(length=128), nullable=False),
        sa.Column("graph_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("tool_schema_version", sa.String(length=64), nullable=False),
        sa.Column("index_generation_id", sa.Uuid(), nullable=True),
        sa.Column("next_event_seq", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "current_execution_seq", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "task_type IN ('question_answer', 'summary', 'structured_extraction')",
            name="task_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_approval', 'succeeded', "
            "'refused', 'failed', 'cancelled', 'rejected', 'expired')",
            name="status_valid",
        ),
        sa.CheckConstraint("next_event_seq > 0", name="next_event_seq_positive"),
        sa.CheckConstraint(
            "current_execution_seq >= 0",
            name="current_execution_seq_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_runs_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_agent_runs_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_agent_runs_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["index_generation_id"],
            ["document_ingestion_generations.id"],
            name="fk_agent_runs_index_generation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_agent_runs_tenant_id_idempotency_key",
        ),
        sa.UniqueConstraint("graph_thread_id", name="uq_agent_runs_graph_thread_id"),
    )
    op.create_index(
        "ix_agent_runs_tenant_id_status_created_at",
        "agent_runs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_tenant_id_document_version_id_created_at",
        "agent_runs",
        ["tenant_id", "document_version_id", "created_at"],
    )

    op.create_table(
        "agent_run_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("public_payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("seq > 0", name="seq_positive"),
        sa.CheckConstraint("event_version > 0", name="event_version_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_run_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_events_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_agent_run_events_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_events"),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_run_events_run_id_seq"),
    )
    op.create_index(
        "ix_agent_run_events_tenant_id_run_id_seq",
        "agent_run_events",
        ["tenant_id", "run_id", "seq"],
    )

    op.create_table(
        "agent_run_evidence",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("rrf_score", sa.Float(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rank > 0", name="rank_positive"),
        sa.CheckConstraint("rrf_score > 0", name="rrf_score_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_run_evidence_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_evidence_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name="fk_agent_run_evidence_chunk_id_document_chunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_agent_run_evidence_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["document_ingestion_generations.id"],
            name="fk_agent_run_evidence_generation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_evidence"),
        sa.UniqueConstraint("run_id", "chunk_id", name="uq_agent_run_evidence_run_id_chunk_id"),
        sa.UniqueConstraint("run_id", "rank", name="uq_agent_run_evidence_run_id_rank"),
    )
    op.create_index(
        "ix_agent_run_evidence_tenant_id_run_id_rank",
        "agent_run_evidence",
        ["tenant_id", "run_id", "rank"],
    )

    op.create_table(
        "agent_artifacts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="writing", nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("object_bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("behavior_versions", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('writing', 'draft_ready', 'published', 'failed', 'revoked')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="size_bytes_non_negative",
        ),
        sa.CheckConstraint(
            "(content_sha256 IS NULL AND size_bytes IS NULL) OR "
            "(content_sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name="content_metadata_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_artifacts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_artifacts_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_version_id"],
            ["document_versions.id"],
            name="fk_agent_artifacts_source_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_artifacts"),
        sa.UniqueConstraint("run_id", "kind", name="uq_agent_artifacts_run_id_kind"),
        sa.UniqueConstraint(
            "tenant_id",
            "object_bucket",
            "object_key",
            name="uq_agent_artifacts_object_location",
        ),
    )
    op.create_index(
        "ix_agent_artifacts_tenant_id_run_id_status",
        "agent_artifacts",
        ["tenant_id", "run_id", "status"],
    )

    op.create_table(
        "approval_requests",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("target_resource_type", sa.String(length=100), nullable=False),
        sa.Column("target_resource_id", sa.Uuid(), nullable=False),
        sa.Column("target_document_version_id", sa.Uuid(), nullable=False),
        sa.Column("target_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("decision_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("decision_comment", sa.String(length=1000), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'revoked', 'consumed')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "operation IN ('publish_artifact')",
            name="operation_valid",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at",
            name="expiry_after_request",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_approval_requests_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_approval_requests_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_actor_id"],
            ["users.id"],
            name="fk_approval_requests_requested_by_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_actor_id"],
            ["users.id"],
            name="fk_approval_requests_decided_by_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_document_version_id"],
            ["document_versions.id"],
            name="fk_approval_requests_target_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_requests"),
        sa.UniqueConstraint(
            "tenant_id",
            "decision_idempotency_key",
            name="uq_approval_requests_tenant_id_decision_idempotency_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "operation",
            "target_resource_type",
            "target_resource_id",
            "target_document_version_id",
            "target_fingerprint",
            name="uq_approval_requests_exact_target",
        ),
    )
    op.create_index(
        "uq_approval_requests_pending_run",
        "approval_requests",
        ["tenant_id", "run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_approval_requests_tenant_id_run_id_status",
        "approval_requests",
        ["tenant_id", "run_id", "status"],
    )

    op.create_table(
        "tool_executions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("target_resource_type", sa.String(length=100), nullable=True),
        sa.Column("target_resource_id", sa.Uuid(), nullable=True),
        sa.Column("target_version", sa.String(length=128), nullable=True),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'denied')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "(target_resource_type IS NULL AND target_resource_id IS NULL) OR "
            "(target_resource_type IS NOT NULL AND target_resource_id IS NOT NULL)",
            name="target_pair_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tool_executions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_tool_executions_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name="fk_tool_executions_approval_request_id_approval_requests",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_executions"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_tool_executions_tenant_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_tool_executions_tenant_id_run_id_created_at",
        "tool_executions",
        ["tenant_id", "run_id", "created_at"],
    )

    op.create_table(
        "agent_run_executions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("resume_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        sa.CheckConstraint(
            "kind IN ('initial', 'resume')",
            name="kind_valid",
        ),
        sa.CheckConstraint(
            "(kind = 'initial' AND sequence = 0 AND approval_request_id IS NULL "
            "AND resume_fingerprint IS NULL) OR "
            "(kind = 'resume' AND sequence > 0 AND approval_request_id IS NOT NULL "
            "AND resume_fingerprint IS NOT NULL)",
            name="resume_shape_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_run_executions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_executions_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_agent_run_executions_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name="fk_agent_run_executions_approval_request_id_approval_requests",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_executions"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_run_executions_run_id_sequence",
        ),
        sa.UniqueConstraint("job_id", name="uq_agent_run_executions_job_id"),
        sa.UniqueConstraint(
            "approval_request_id",
            name="uq_agent_run_executions_approval_request_id",
        ),
    )
    op.create_index(
        "ix_agent_run_executions_tenant_id_run_id_sequence",
        "agent_run_executions",
        ["tenant_id", "run_id", "sequence"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    contains_rows = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM agent_run_executions
                UNION ALL SELECT 1 FROM agent_run_events
                UNION ALL SELECT 1 FROM agent_run_evidence
                UNION ALL SELECT 1 FROM approval_requests
                UNION ALL SELECT 1 FROM tool_executions
                UNION ALL SELECT 1 FROM agent_artifacts
                UNION ALL SELECT 1 FROM agent_runs
            )
            """
        )
    ).scalar_one()
    if contains_rows:
        raise RuntimeError("cannot downgrade while M4 Agent rows exist")

    op.drop_index(
        "ix_agent_run_executions_tenant_id_run_id_sequence",
        table_name="agent_run_executions",
    )
    op.drop_table("agent_run_executions")
    op.drop_index(
        "ix_tool_executions_tenant_id_run_id_created_at",
        table_name="tool_executions",
    )
    op.drop_table("tool_executions")
    op.drop_index(
        "ix_approval_requests_tenant_id_run_id_status",
        table_name="approval_requests",
    )
    op.drop_index("uq_approval_requests_pending_run", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index(
        "ix_agent_artifacts_tenant_id_run_id_status",
        table_name="agent_artifacts",
    )
    op.drop_table("agent_artifacts")
    op.drop_index(
        "ix_agent_run_evidence_tenant_id_run_id_rank",
        table_name="agent_run_evidence",
    )
    op.drop_table("agent_run_evidence")
    op.drop_index(
        "ix_agent_run_events_tenant_id_run_id_seq",
        table_name="agent_run_events",
    )
    op.drop_table("agent_run_events")
    op.drop_index(
        "ix_agent_runs_tenant_id_document_version_id_created_at",
        table_name="agent_runs",
    )
    op.drop_index(
        "ix_agent_runs_tenant_id_status_created_at",
        table_name="agent_runs",
    )
    op.drop_table("agent_runs")
