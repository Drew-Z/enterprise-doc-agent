"""Record presales behavior provenance and uncertain dispatches.

Revision ID: 20260912_0022
Revises: 20260912_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0022"
down_revision: str | None = "20260912_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "presales_attempts",
        sa.Column("provenance", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.alter_column(
        "presales_attempts", "provider_request_count", existing_type=sa.Integer(), nullable=True
    )


def downgrade() -> None:
    # Unknown request counts cannot safely become zero during a rollback.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM presales_attempts WHERE provider_request_count IS NULL")
    ):
        raise RuntimeError("Resolve uncertain presales dispatches before downgrading.")
    op.alter_column(
        "presales_attempts", "provider_request_count", existing_type=sa.Integer(), nullable=False
    )
    op.drop_column("presales_attempts", "provenance")
