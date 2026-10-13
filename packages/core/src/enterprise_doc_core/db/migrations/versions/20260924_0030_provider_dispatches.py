"""Persist provider HTTP attempts separately from business task quotas."""

import sqlalchemy as sa
from alembic import op

revision = "20260924_0030"
down_revision = "20260924_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("channel_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(24, 8)),
        sa.Column("currency", sa.String(8)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('agent', 'document', 'query')", name="kind_valid"),
        sa.CheckConstraint(
            "state IN ('dispatched', 'responded', 'http_error', 'timeout', "
            "'transport_error', 'cancelled', 'unknown')",
            name="state_valid",
        ),
        sa.CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="tokens_nonnegative"),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0", name="cost_nonnegative"
        ),
    )
    op.create_index(
        "ix_provider_dispatches_tenant_time", "provider_dispatches", ["tenant_id", "started_at"]
    )
    op.create_index(
        "ix_provider_dispatches_operation",
        "provider_dispatches",
        ["tenant_id", "kind", "operation_id"],
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM provider_dispatches)")).scalar():
        raise RuntimeError("provider_dispatch_history_present")
    op.drop_table("provider_dispatches")
