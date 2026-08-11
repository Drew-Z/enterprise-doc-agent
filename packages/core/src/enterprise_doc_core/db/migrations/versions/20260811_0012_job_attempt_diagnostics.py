"""Add bounded diagnostic codes to durable job attempts.

Revision ID: 20260811_0012
Revises: 20260804_0011
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260811_0012"
down_revision = "20260804_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_attempts", sa.Column("diagnostic_code", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job_attempts", "diagnostic_code")
