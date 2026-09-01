"""Add audit retention policy and legal hold controls.

Revision ID: 20260825_0017
Revises: 20260825_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "20260825_0017"
down_revision = "20260825_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_retention_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("365"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
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
            "retention_days >= 30 AND retention_days <= 3650",
            name="audit_retention_days_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_audit_retention_policies_tenant_id"),
    )
    op.create_table(
        "audit_legal_holds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "starts_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
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
            "(resource_type IS NULL AND resource_id IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name="audit_legal_hold_scope_valid",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > starts_at",
            name="audit_legal_hold_expiry_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_legal_holds_tenant_id_released_at_expires_at",
        "audit_legal_holds",
        ["tenant_id", "released_at", "expires_at"],
    )
    op.create_index(
        "ix_audit_legal_holds_tenant_id_resource",
        "audit_legal_holds",
        ["tenant_id", "resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_legal_holds_tenant_id_resource", table_name="audit_legal_holds")
    op.drop_index(
        "ix_audit_legal_holds_tenant_id_released_at_expires_at",
        table_name="audit_legal_holds",
    )
    op.drop_table("audit_legal_holds")
    op.drop_table("audit_retention_policies")
