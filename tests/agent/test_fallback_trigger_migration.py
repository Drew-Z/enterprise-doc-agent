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
    / "20260818_0014_agent_run_fallback_trigger.py"
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


def _has_fallback_trigger_column() -> bool:
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'agent_runs' "
            "AND column_name = 'fallback_trigger_code')"
        )
        row = cursor.fetchone()
        return bool(row and row[0])


def test_fallback_trigger_migration_is_additive_and_nullable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260818_0014"' in source
    assert 'down_revision = "20260817_0013"' in source
    assert 'sa.Column("fallback_trigger_code", sa.String(length=100), nullable=True)' in source
    assert 'op.drop_column("agent_runs", "fallback_trigger_code")' in source


@pytest.mark.integration
def test_fallback_trigger_migration_round_trips_column() -> None:
    try:
        _run_alembic("downgrade", "20260817_0013")
        assert _has_fallback_trigger_column() is False

        _run_alembic("upgrade", "20260818_0014")
        assert _has_fallback_trigger_column() is True
    finally:
        _run_alembic("upgrade", "head")
