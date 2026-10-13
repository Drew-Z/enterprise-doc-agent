"""Durable presales operations, bounded dispatch history and route health."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260924_0028"
down_revision = "20260923_0027"
branch_labels = None
depends_on = None


def _constraints(background: bool) -> None:
    for name in ("presales_attempt_state_valid", "presales_attempt_limits"):
        op.drop_constraint(op.f(f"ck_presales_attempts_{name}"), "presales_attempts", type_="check")
    states = (
        "'queued', 'running', 'recovering', 'succeeded', 'failed', 'expired'"
        if background
        else "'running', 'succeeded', 'failed', 'expired'"
    )
    op.create_check_constraint(
        op.f("ck_presales_attempts_presales_attempt_state_valid"),
        "presales_attempts",
        f"state IN ({states})",
    )
    op.create_check_constraint(
        op.f("ck_presales_attempts_presales_attempt_limits"),
        "presales_attempts",
        f"number BETWEEN 1 AND 3 AND provider_request_count BETWEEN 0 AND {2 if background else 1}",
    )


def upgrade() -> None:
    _constraints(True)
    op.add_column("presales_attempts", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.add_column(
        "presales_attempts", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_presales_attempts_job_id_jobs",
        "presales_attempts",
        "jobs",
        ["job_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_unique_constraint("uq_presales_attempts_job_id", "presales_attempts", ["job_id"])
    op.create_unique_constraint(
        "uq_presales_attempts_tenant_id", "presales_attempts", ["tenant_id", "id"]
    )
    op.create_table(
        "presales_provider_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("route", sa.String(16), nullable=False),
        sa.Column("route_key", sa.String(64), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("health_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("provider_response_id", sa.String(200), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            ["presales_attempts.tenant_id", "presales_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("operation_id", "number", name="uq_presales_provider_calls_number"),
        sa.CheckConstraint("number BETWEEN 1 AND 2", name="presales_provider_call_limit"),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'unknown', 'not_sent')",
            name="presales_provider_call_state",
        ),
    )
    op.create_index("ix_presales_provider_calls_started", "presales_provider_calls", ["started_at"])
    op.create_table(
        "presales_route_health",
        sa.Column("route_key", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probe_call_id", sa.Uuid(), nullable=True),
        sa.Column("probe_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "presales_dispatch_days",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("dispatched", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM presales_attempts WHERE job_id IS NOT NULL "
            "OR state IN ('queued','recovering') OR provider_request_count > 1) "
            "OR EXISTS(SELECT 1 FROM presales_provider_calls) "
            "OR EXISTS(SELECT 1 FROM presales_dispatch_days WHERE dispatched > 0)"
        )
    ):
        raise RuntimeError(
            "Preserve background generation history; roll back configuration instead."
        )
    op.drop_table("presales_provider_calls")
    op.drop_table("presales_route_health")
    op.drop_table("presales_dispatch_days")
    op.drop_constraint("uq_presales_attempts_tenant_id", "presales_attempts", type_="unique")
    op.drop_constraint("uq_presales_attempts_job_id", "presales_attempts", type_="unique")
    op.drop_constraint("fk_presales_attempts_job_id_jobs", "presales_attempts", type_="foreignkey")
    op.drop_column("presales_attempts", "job_id")
    op.drop_column("presales_attempts", "started_at")
    _constraints(False)
