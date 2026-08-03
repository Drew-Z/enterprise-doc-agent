"""Replace deterministic eight-dimensional vectors with semantic embeddings.

Revision ID: 20260803_0010
Revises: 20260718_0009
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20260803_0010"
down_revision = "20260718_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DIMENSION = 8
NEW_DIMENSION = 1024
INDEX_NAME = "ix_document_chunks_embedding_hnsw"


def upgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="document_chunks")
    op.execute(sa.text("UPDATE document_chunks SET embedding = NULL"))
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(OLD_DIMENSION),
        type_=Vector(NEW_DIMENSION),
        existing_nullable=True,
        postgresql_using=f"embedding::vector({NEW_DIMENSION})",
    )
    op.create_index(
        INDEX_NAME,
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="document_chunks")
    op.execute(sa.text("UPDATE document_chunks SET embedding = NULL"))
    op.alter_column(
        "document_chunks",
        "embedding",
        existing_type=Vector(NEW_DIMENSION),
        type_=Vector(OLD_DIMENSION),
        existing_nullable=True,
        postgresql_using=f"embedding::vector({OLD_DIMENSION})",
    )
    op.create_index(
        INDEX_NAME,
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
