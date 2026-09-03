"""Add tenant-scoped local JWT revocation records.

Revision ID: 20260827_0020
Revises: 20260826_0019
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0020"
down_revision = "20260826_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_token_revocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("token_id", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "reason", sa.String(length=80), server_default=sa.text("'logout'"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "token_id",
            name="uq_local_token_revocations_tenant_token",
        ),
    )
    op.create_index(
        "ix_local_token_revocations_expires_at",
        "local_token_revocations",
        ["expires_at"],
    )
    op.create_index(
        "ix_local_token_revocations_tenant_actor_revoked_at",
        "local_token_revocations",
        ["tenant_id", "actor_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_local_token_revocations_tenant_actor_revoked_at",
        table_name="local_token_revocations",
    )
    op.drop_index("ix_local_token_revocations_expires_at", table_name="local_token_revocations")
    op.drop_table("local_token_revocations")
