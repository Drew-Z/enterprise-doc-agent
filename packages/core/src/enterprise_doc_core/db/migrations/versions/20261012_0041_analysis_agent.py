"""Add bounded analysis tool receipts and resumable input requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0041"
down_revision = "20261012_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_input_requests",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submission_hash", sa.String(length=64), nullable=True),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
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
            "revision > 0", name=op.f("ck_analysis_input_requests_revision_positive")
        ),
        sa.CheckConstraint(
            "state IN ('open','submitted','cancelled')",
            name=op.f("ck_analysis_input_requests_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_input_requests_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_input_requests")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_input_request_scope"),
        sa.UniqueConstraint("unit_id", "revision", name="uq_analysis_input_request_revision"),
    )
    op.create_index(
        op.f("ix_analysis_input_requests_unit_id"),
        "analysis_input_requests",
        ["unit_id"],
        unique=False,
    )
    op.create_table(
        "analysis_tool_executions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=32), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
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
            "ordinal BETWEEN 1 AND 3",
            name=op.f("ck_analysis_tool_executions_ordinal_valid"),
        ),
        sa.CheckConstraint(
            "state IN ('requested','waiting_input','completed','failed')",
            name=op.f("ck_analysis_tool_executions_state_valid"),
        ),
        sa.CheckConstraint(
            "tool_name IN ('search_sources','read_source','calculate')",
            name=op.f("ck_analysis_tool_executions_tool_name_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id"],
            ["analysis_units.tenant_id", "analysis_units.id"],
            name=op.f("fk_analysis_tool_executions_tenant_id_unit_id_analysis_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_tool_executions")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_analysis_tool_execution_scope"),
        sa.UniqueConstraint("unit_id", "ordinal", name="uq_analysis_tool_execution_ordinal"),
    )
    op.create_index(
        op.f("ix_analysis_tool_executions_unit_id"),
        "analysis_tool_executions",
        ["unit_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE analysis_input_requests, analysis_tool_executions IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_input_requests) OR "
            "EXISTS (SELECT 1 FROM analysis_tool_executions)"
        )
    ):
        raise RuntimeError("analysis_agent_history_present")
    op.drop_index(
        op.f("ix_analysis_tool_executions_unit_id"), table_name="analysis_tool_executions"
    )
    op.drop_table("analysis_tool_executions")
    op.drop_index(op.f("ix_analysis_input_requests_unit_id"), table_name="analysis_input_requests")
    op.drop_table("analysis_input_requests")
