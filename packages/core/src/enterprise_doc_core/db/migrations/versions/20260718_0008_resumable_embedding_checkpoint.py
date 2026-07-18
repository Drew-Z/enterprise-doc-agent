"""Allow persisted chunks before embedding completes.

Revision ID: 20260718_0008
Revises: 20260718_0007
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20260718_0008"
down_revision = "20260718_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSION = 8
COUNT_CONSTRAINT = "embedded_count_within_chunk_count"


def upgrade() -> None:
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(EMBEDDING_DIMENSION),
        nullable=True,
    )
    op.create_check_constraint(
        COUNT_CONSTRAINT,
        "document_ingestion_generations",
        "embedded_count <= chunk_count",
    )


def downgrade() -> None:
    bind = op.get_bind()
    incomplete_chunks = bind.execute(
        sa.text("SELECT count(*) FROM document_chunks WHERE embedding IS NULL")
    ).scalar_one()
    if incomplete_chunks:
        raise RuntimeError(
            "cannot downgrade while resumable embedding checkpoints contain NULL vectors"
        )
    op.drop_constraint(
        COUNT_CONSTRAINT,
        "document_ingestion_generations",
        type_="check",
    )
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(EMBEDDING_DIMENSION),
        nullable=False,
    )
