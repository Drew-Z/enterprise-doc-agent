"""Add upload part observation ordering.

Revision ID: 20260717_0003
Revises: 20260717_0002
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260717_0003"
down_revision = "20260717_0002"
branch_labels = None
depends_on = None

_OBSERVATION_VERSION_SEQUENCE = sa.Sequence("upload_part_observation_version_seq")


def upgrade() -> None:
    _OBSERVATION_VERSION_SEQUENCE.create(op.get_bind())
    op.add_column(
        "upload_parts",
        sa.Column("observation_version", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "upload_parts",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("upload_parts", "observed_at")
    op.drop_column("upload_parts", "observation_version")
    _OBSERVATION_VERSION_SEQUENCE.drop(op.get_bind())
