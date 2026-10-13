"""Add fixed analysis execution and a separate analysis-cell entitlement."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0040"
down_revision = "20261011_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_provider_dispatch_operation_scope",
        "provider_dispatches",
        ["tenant_id", "operation_id", "id"],
    )
    op.drop_constraint(op.f("ck_product_quotas_metric_valid"), "product_quotas", type_="check")
    op.create_check_constraint(
        op.f("ck_product_quotas_metric_valid"),
        "product_quotas",
        "metric IN ('agent_task', 'document_bytes', 'analysis_cell')",
    )
    op.drop_constraint(
        op.f("ck_product_usage_reservations_one_task"), "product_usage_reservations", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_product_usage_reservations_one_task"),
        "product_usage_reservations",
        "metric NOT IN ('agent_task', 'analysis_cell') OR quantity = 1",
    )
    op.create_table(
        "analysis_runs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('queued','running','waiting_input',"
            "'completed','partial','failed','cancelled','expired')",
            name=op.f("ck_analysis_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "total_units BETWEEN 1 AND 30", name=op.f("ck_analysis_runs_unit_count_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["jobs.tenant_id", "jobs.id"],
            name=op.f("fk_analysis_runs_tenant_id_job_id_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["analysis_projects.tenant_id", "analysis_projects.id"],
            name=op.f("fk_analysis_runs_tenant_id_project_id_analysis_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["analysis_source_snapshots.tenant_id", "analysis_source_snapshots.id"],
            name=op.f("fk_analysis_runs_tenant_id_snapshot_id_analysis_source_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_run_scope"),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "idempotency_key", name="uq_analysis_run_key"
        ),
    )
    op.create_index(
        op.f("ix_analysis_runs_project_id"), "analysis_runs", ["project_id"], unique=False
    )
    op.create_table(
        "analysis_units",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("field_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("evidence_set", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('queued','running','waiting_input',"
            "'succeeded','failed','cancelled','expired')",
            name=op.f("ck_analysis_units_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            name=op.f("fk_analysis_units_tenant_id_run_id_analysis_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_units")),
        sa.UniqueConstraint("run_id", "object_id", "field_id", name="uq_analysis_unit_field"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_unit_scope"),
    )
    op.create_index(op.f("ix_analysis_units_run_id"), "analysis_units", ["run_id"], unique=False)
    op.create_table(
        "analysis_actions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("provider_dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
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
            "state IN ('dispatched','retryable','completed','failed','unknown')",
            name=op.f("ck_analysis_actions_state_valid"),
        ),
        sa.CheckConstraint("slot BETWEEN 1 AND 2", name=op.f("ck_analysis_actions_slot_valid")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id", "provider_dispatch_id"],
            [
                "provider_dispatches.tenant_id",
                "provider_dispatches.operation_id",
                "provider_dispatches.id",
            ],
            name=op.f(
                "fk_analysis_actions_tenant_id_unit_id_provider_dispatch_id_provider_dispatches"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_actions_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_actions")),
        sa.UniqueConstraint("provider_dispatch_id", name="uq_analysis_dispatch_action"),
        sa.UniqueConstraint("unit_id", "slot", name="uq_analysis_action_slot"),
    )
    op.create_table(
        "analysis_cell_revisions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            "revision > 0", name=op.f("ck_analysis_cell_revisions_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_cell_revisions_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_cell_revisions")),
        sa.UniqueConstraint("unit_id", "revision", name="uq_analysis_cell_revision"),
    )
    op.create_table(
        "analysis_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["analysis_runs.tenant_id", "analysis_runs.id"],
            name=op.f("fk_analysis_events_tenant_id_run_id_analysis_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_events")),
        sa.UniqueConstraint("run_id", "sequence", name="uq_analysis_event_sequence"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE analysis_runs, analysis_units, analysis_actions, "
            "analysis_cell_revisions, analysis_events, product_quotas IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_runs) OR EXISTS "
            "(SELECT 1 FROM product_quotas WHERE metric = 'analysis_cell')"
        )
    ):
        raise RuntimeError("analysis_execution_history_present")
    for name in (
        "analysis_events",
        "analysis_cell_revisions",
        "analysis_actions",
        "analysis_units",
        "analysis_runs",
    ):
        op.drop_table(name)
    op.drop_constraint(
        "uq_provider_dispatch_operation_scope", "provider_dispatches", type_="unique"
    )
    op.drop_constraint(op.f("ck_product_quotas_metric_valid"), "product_quotas", type_="check")
    op.create_check_constraint(
        op.f("ck_product_quotas_metric_valid"),
        "product_quotas",
        "metric IN ('agent_task', 'document_bytes')",
    )
    op.drop_constraint(
        op.f("ck_product_usage_reservations_one_task"), "product_usage_reservations", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_product_usage_reservations_one_task"),
        "product_usage_reservations",
        "metric != 'agent_task' OR quantity = 1",
    )
