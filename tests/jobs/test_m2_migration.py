from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260718_0006_durable_job_runtime.py"
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


def test_m2_migration_is_additive_after_m1_head() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260718_0006"' in source
    assert 'down_revision = "20260717_0005"' in source
    for table in ("jobs", "job_attempts", "job_events", "outbox_events"):
        assert f'op.create_table(\n        "{table}"' in source


@pytest.mark.integration
def test_m2_migration_creates_and_removes_durable_job_tables() -> None:
    _reset_test_schema()
    _run_alembic("upgrade", "20260718_0006")
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('jobs', 'job_attempts', 'job_events', 'outbox_events')
            """
        )
        assert {row[0] for row in cursor.fetchall()} == {
            "jobs",
            "job_attempts",
            "job_events",
            "outbox_events",
        }

    _run_alembic("downgrade", "20260717_0005")
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('jobs', 'job_attempts', 'job_events', 'outbox_events')
            """
        )
        assert cursor.fetchall() == []

    _run_alembic("upgrade", "head")
    _restore_checkpoint_schema()
