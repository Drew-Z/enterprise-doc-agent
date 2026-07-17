"""Add leased upload cleanup claims.

Revision ID: 20260717_0005
Revises: 20260717_0004
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260717_0005"
down_revision = "20260717_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "upload_sessions",
        sa.Column("cleanup_claim_token", sa.Uuid(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_upload_sessions_cleanup_claim_pair"),
        "upload_sessions",
        "(cleanup_claimed_at IS NULL AND cleanup_claim_token IS NULL) OR "
        "(cleanup_claimed_at IS NOT NULL AND cleanup_claim_token IS NOT NULL)",
    )
    op.create_index(
        "ix_upload_sessions_status_cleanup_claimed_at",
        "upload_sessions",
        ["status", "cleanup_claimed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_upload_sessions_status_cleanup_claimed_at",
        table_name="upload_sessions",
    )
    op.drop_constraint(
        op.f("ck_upload_sessions_cleanup_claim_pair"),
        "upload_sessions",
        type_="check",
    )
    op.drop_column("upload_sessions", "cleanup_claim_token")
    op.drop_column("upload_sessions", "cleanup_claimed_at")
