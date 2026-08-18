"""Persist the primary failure that triggered model fallback.

Revision ID: 20260818_0014
Revises: 20260817_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0014"
down_revision = "20260817_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("fallback_trigger_code", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "fallback_trigger_code")
