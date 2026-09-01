"""Add tenant-scoped append-only governance audit events.

Revision ID: 20260825_0015
Revises: 20260818_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20260825_0015"
down_revision = "20260818_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_tenant_id_occurred_at_id",
        "audit_events",
        ["tenant_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_events_tenant_id_action_occurred_at",
        "audit_events",
        ["tenant_id", "action", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_tenant_id_resource_occurred_at",
        "audit_events",
        ["tenant_id", "resource_type", "resource_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_id_resource_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_id_action_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_id_occurred_at_id", table_name="audit_events")
    op.drop_table("audit_events")
