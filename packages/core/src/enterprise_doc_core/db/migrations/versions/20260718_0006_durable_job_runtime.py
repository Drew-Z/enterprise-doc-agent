"""Add durable jobs, attempts, events, and transactional outbox.

Revision ID: 20260718_0006
Revises: 20260717_0005
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "20260718_0006"
down_revision = "20260717_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="attempts_valid",
        ),
        sa.CheckConstraint("fencing_token >= 0", name="fencing_token_non_negative"),
        sa.CheckConstraint(
            "(locked_by IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(locked_by IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="lease_pair",
        ),
        sa.CheckConstraint("payload_version > 0", name="payload_version_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'succeeded', 'dead', 'cancelled')",
            name="status_valid",
        ),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_jobs_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_jobs_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_jobs_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint(
            "document_version_id",
            name="uq_jobs_document_version_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_jobs_tenant_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["status", "available_at", sa.text("priority DESC"), "id"],
    )
    op.create_index("ix_jobs_lease_expiry", "jobs", ["status", "lease_expires_at"])
    op.create_index(
        "ix_jobs_tenant_id_status_created_at",
        "jobs",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "job_attempts",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("error_class", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        sa.CheckConstraint("fencing_token > 0", name="fencing_token_positive"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retryable_failed', 'permanent_failed', "
            "'abandoned', 'cancelled')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_attempts_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_job_attempts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_attempts"),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_job_attempts_job_id_attempt_number",
        ),
    )
    op.create_index(
        "ix_job_attempts_tenant_id_job_id_attempt_number",
        "job_attempts",
        ["tenant_id", "job_id", "attempt_number"],
    )

    op.create_table(
        "job_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("payload_version > 0", name="payload_version_positive"),
        sa.CheckConstraint("seq > 0", name="seq_positive"),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_job_events_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_events_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_job_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("job_id", "seq", name="uq_job_events_job_id_seq"),
    )
    op.create_index(
        "ix_job_events_tenant_id_job_id_seq",
        "job_events",
        ["tenant_id", "job_id", "seq"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint(
            "(locked_by IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(locked_by IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="lease_pair",
        ),
        sa.CheckConstraint("payload_version > 0", name="payload_version_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'dead')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["jobs.id"],
            name="fk_outbox_events_aggregate_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_outbox_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index(
        "ix_outbox_events_publishable",
        "outbox_events",
        ["status", "available_at", "id"],
    )
    op.create_index(
        "ix_outbox_events_lease_expiry",
        "outbox_events",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_outbox_events_tenant_id_created_at",
        "outbox_events",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_tenant_id_created_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_lease_expiry", table_name="outbox_events")
    op.drop_index("ix_outbox_events_publishable", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_job_events_tenant_id_job_id_seq", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index(
        "ix_job_attempts_tenant_id_job_id_attempt_number",
        table_name="job_attempts",
    )
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_tenant_id_status_created_at", table_name="jobs")
    op.drop_index("ix_jobs_lease_expiry", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")
