"""Add bounded anonymous demo workspaces, separate from external identity."""

import sqlalchemy as sa
from alembic import op

revision = "20260923_0027"
down_revision = "20260914_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_days",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("workspaces_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workspace_limit", sa.Integer(), nullable=False),
        sa.Column("attempt_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint("workspaces_created >= 0 AND attempts_used >= 0", name="usage_positive"),
    )
    op.create_table(
        "demo_workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="SET NULL"), unique=True
        ),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), unique=True
        ),
        sa.Column("credential_digest", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
        sa.Column("attempts_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_attempt_id", sa.Uuid()),
        sa.Column("busy_until", sa.DateTime(timezone=True)),
        sa.Column("daily_attempt_limit", sa.Integer(), nullable=False),
        sa.Column("daily_workspace_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempts_used >= 0 AND attempts_used <= 6", name="attempts_bounded"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_order"),
    )
    op.create_index("ix_demo_workspaces_expires_at", "demo_workspaces", ["expires_at"])


def downgrade() -> None:
    op.drop_table("demo_workspaces")
    op.drop_table("demo_days")
