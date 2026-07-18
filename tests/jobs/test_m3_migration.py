from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
M3_MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260718_0007_document_rag.py"
)
HARDENING_MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260718_0008_resumable_embedding_checkpoint.py"
)
DATABASE_URL = os.environ.get(
    "FOUNDATION_TEST_DATABASE_URL",
    "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
)


def _run_alembic(*arguments: str) -> None:
    subprocess.run(
        ["uv", "run", "alembic", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_m3_migration_is_additive_after_m2_head() -> None:
    source = M3_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260718_0007"' in source
    assert 'down_revision = "20260718_0006"' in source
    assert '"document_ingestion_generations"' in source
    assert '"document_chunks"' in source
    assert "Vector(EMBEDDING_DIMENSION)" in source


def test_m3_hardening_migration_is_additive_after_document_rag() -> None:
    source = HARDENING_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260718_0008"' in source
    assert 'down_revision = "20260718_0007"' in source
    assert '"embedding"' in source
    assert "nullable=True" in source
    assert "embedded_count <= chunk_count" in source


@pytest.mark.integration
def test_m3_migration_creates_hybrid_rag_schema_and_downgrades() -> None:
    _run_alembic("upgrade", "head")
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('document_ingestion_generations', 'document_chunks')
            """
        )
        assert {row[0] for row in cursor.fetchall()} == {
            "document_ingestion_generations",
            "document_chunks",
        }
        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'document_chunks'
              AND indexname IN ('ix_document_chunks_fts', 'ix_document_chunks_embedding_hnsw')
            """
        )
        assert {row[0] for row in cursor.fetchall()} == {
            "ix_document_chunks_fts",
            "ix_document_chunks_embedding_hnsw",
        }
        cursor.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'document_chunks'
              AND column_name = 'embedding'
            """
        )
        assert cursor.fetchone() == ("YES",)
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'document_ingestion_generations'::regclass
              AND pg_get_constraintdef(oid) = 'CHECK ((embedded_count <= chunk_count))'
            """
        )
        assert cursor.fetchone() is not None

    _run_alembic("downgrade", "20260718_0006")
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('document_ingestion_generations', 'document_chunks')
            """
        )
        assert cursor.fetchall() == []

    _run_alembic("upgrade", "head")
