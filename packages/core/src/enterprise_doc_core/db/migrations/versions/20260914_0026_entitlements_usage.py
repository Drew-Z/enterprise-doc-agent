"""Add versioned tenant entitlements and provider request usage ledger."""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0026"
down_revision = "20260914_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_entitlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_code", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_request_limit", sa.BigInteger()),
        sa.Column(
            "provider_requests_used",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "provider_requests_reserved",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_entitlements_tenant_id"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tenant_entitlements_version"),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint("period_end > period_start", name="valid_period"),
        sa.CheckConstraint(
            "provider_request_limit IS NULL OR provider_request_limit >= 0",
            name="provider_request_limit_non_negative",
        ),
        sa.CheckConstraint(
            "provider_requests_used >= 0 AND provider_requests_reserved >= 0",
            name="provider_request_counters_non_negative",
        ),
    )
    op.create_index(
        "ix_tenant_entitlements_current",
        "tenant_entitlements",
        ["tenant_id", "period_start", "period_end"],
    )
    op.create_table(
        "usage_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False, server_default="provider_request"),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_usage_reservations_tenant_id"),
        sa.UniqueConstraint("tenant_id", "operation_id", name="uq_usage_reservations_operation"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("metric = 'provider_request'", name="provider_request_metric"),
        sa.CheckConstraint("quantity > 0", name="positive_quantity"),
        sa.CheckConstraint(
            "state IN ('reserved', 'consumed', 'released')", name="reservation_state_valid"
        ),
    )
    op.create_index(
        "ix_usage_reservations_expiry",
        "usage_reservations",
        ["tenant_id", "entitlement_id", "state", "expires_at"],
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False, server_default="provider_request"),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(80)),
        sa.Column("model", sa.String(200)),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("currency", sa.String(12)),
        sa.Column("estimated_cost", sa.Numeric(18, 8)),
        sa.Column("pricing_version", sa.String(80)),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_usage_events_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "operation_id", "event_type", name="uq_usage_events_operation_type"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reservation_id"],
            ["usage_reservations.tenant_id", "usage_reservations.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("event_type IN ('consume', 'release')", name="usage_event_type_valid"),
        sa.CheckConstraint("quantity > 0", name="usage_event_quantity_positive"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="usage_event_input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="usage_event_output_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="usage_event_total_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0", name="usage_event_cost_non_negative"
        ),
    )
    op.create_index("ix_usage_events_tenant_time", "usage_events", ["tenant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("usage_reservations")
    op.drop_index("ix_tenant_entitlements_current", table_name="tenant_entitlements")
    op.drop_table("tenant_entitlements")
