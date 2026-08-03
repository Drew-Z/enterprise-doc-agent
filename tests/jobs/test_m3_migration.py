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
SEMANTIC_EMBEDDING_MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260803_0010_semantic_embeddings.py"
)
REINDEXABLE_JOBS_MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260804_0011_reindexable_jobs.py"
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


def _reset_test_schema() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")


def _restore_checkpoint_schema() -> None:
    subprocess.run(
        ["uv", "run", "enterprise-doc-checkpointer-setup", "--setup"],
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


def test_semantic_embedding_migration_replaces_the_fixture_vector_index() -> None:
    source = SEMANTIC_EMBEDDING_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260803_0010"' in source
    assert 'down_revision = "20260718_0009"' in source
    assert "OLD_DIMENSION = 8" in source
    assert "NEW_DIMENSION = 1024" in source
    assert "UPDATE document_chunks SET embedding = NULL" in source


def test_reindexable_jobs_migration_removes_the_one_job_per_version_constraint() -> None:
    source = REINDEXABLE_JOBS_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260804_0011"' in source
    assert 'down_revision = "20260803_0010"' in source
    assert 'UNIQUE_CONSTRAINT = "uq_jobs_document_version_id"' in source
    assert 'LOOKUP_INDEX = "ix_jobs_document_version_id_created_at"' in source


@pytest.mark.integration
def test_m3_migration_creates_hybrid_rag_schema_and_downgrades() -> None:
    _reset_test_schema()
    _run_alembic("upgrade", "20260718_0008")
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
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute AS a
            WHERE a.attrelid = 'document_chunks'::regclass
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
        assert cursor.fetchone() == ("vector(8)",)
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
    _restore_checkpoint_schema()
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute AS a
            WHERE a.attrelid = 'document_chunks'::regclass
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
        assert cursor.fetchone() == ("vector(1024)",)
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'jobs'::regclass
              AND conname = 'uq_jobs_document_version_id'
            """
        )
        assert cursor.fetchone() is None
        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'jobs'
              AND indexname = 'ix_jobs_document_version_id_created_at'
            """
        )
        assert cursor.fetchone() == ("ix_jobs_document_version_id_created_at",)
