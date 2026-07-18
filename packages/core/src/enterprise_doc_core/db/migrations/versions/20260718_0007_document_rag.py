"""Add versioned document ingestion generations and hybrid-RAG chunks.

Revision ID: 20260718_0007
Revises: 20260718_0006
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "20260718_0007"
down_revision = "20260718_0006"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSION = 8


def upgrade() -> None:
    op.create_table(
        "document_ingestion_generations",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("parser_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("chunker_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("embedding_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), server_default="hash", nullable=False),
        sa.Column(
            "embedding_dimension",
            sa.Integer(),
            server_default=sa.text(str(EMBEDDING_DIMENSION)),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(length=30), server_default="download_spool", nullable=False),
        sa.Column("stage_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedded_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')", name="status_valid"
        ),
        sa.CheckConstraint(
            "stage IN ('download_spool', 'parse', 'chunk', 'embed', 'index', 'ready')",
            name="stage_valid",
        ),
        sa.CheckConstraint("parser_version > 0", name="parser_version_positive"),
        sa.CheckConstraint("chunker_version > 0", name="chunker_version_positive"),
        sa.CheckConstraint("embedding_version > 0", name="embedding_version_positive"),
        sa.CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
        sa.CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        sa.CheckConstraint("embedded_count >= 0", name="embedded_count_non_negative"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_doc_ingest_generations_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_doc_ingest_generations_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "parser_version",
            "chunker_version",
            "embedding_version",
            name="uq_document_ingestion_generations_version_key",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_ingestion_generations"),
    )
    op.create_index(
        "ix_document_ingestion_generations_tenant_version_status",
        "document_ingestion_generations",
        ["tenant_id", "document_version_id", "status"],
    )
    op.create_index(
        "uq_document_ingestion_generations_active_version",
        "document_ingestion_generations",
        ["tenant_id", "document_version_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "document_chunks",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=500), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("start_offset", sa.BigInteger(), nullable=False),
        sa.Column("end_offset", sa.BigInteger(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("search_vector", TSVECTOR(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset", name="offsets_valid"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_doc_chunks_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_doc_chunks_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["document_ingestion_generations.id"],
            name="fk_doc_chunks_generation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "generation_id", "chunk_index", name="uq_document_chunks_generation_index"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
    )
    op.create_index(
        "ix_document_chunks_tenant_version_generation",
        "document_chunks",
        ["tenant_id", "document_version_id", "generation_id"],
    )
    op.create_index(
        "ix_document_chunks_generation_chunk_index",
        "document_chunks",
        ["generation_id", "chunk_index"],
    )
    op.create_index(
        "ix_document_chunks_fts", "document_chunks", ["search_vector"], postgresql_using="gin"
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding_hnsw", table_name="document_chunks")
    op.drop_index("ix_document_chunks_fts", table_name="document_chunks")
    op.drop_index("ix_document_chunks_generation_chunk_index", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tenant_version_generation", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(
        "uq_document_ingestion_generations_active_version",
        table_name="document_ingestion_generations",
    )
    op.drop_index(
        "ix_document_ingestion_generations_tenant_version_status",
        table_name="document_ingestion_generations",
    )
    op.drop_table("document_ingestion_generations")
