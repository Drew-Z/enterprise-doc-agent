"""Add explicit external issuer/subject identity bindings.

Revision ID: 20260826_0019
Revises: 20260826_0018
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0019"
down_revision = "20260826_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identity_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "issuer",
            "subject",
            name="uq_external_identity_bindings_tenant_issuer_subject",
        ),
    )
    op.create_index(
        "ix_external_identity_bindings_tenant_id_user_id_is_active",
        "external_identity_bindings",
        ["tenant_id", "user_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_identity_bindings_tenant_id_user_id_is_active",
        table_name="external_identity_bindings",
    )
    op.drop_table("external_identity_bindings")
