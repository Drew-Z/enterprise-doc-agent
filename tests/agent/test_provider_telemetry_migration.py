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
    / "20260817_0013_agent_run_provider_telemetry.py"
)
DATABASE_URL = os.environ.get(
    "FOUNDATION_TEST_DATABASE_URL",
    "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
)
TELEMETRY_COLUMNS = {
    "model_revision",
    "provider_request_count",
    "provider_usage_request_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "repair_request_count",
    "fallback_count",
    "breaker_state",
}


def _run_alembic(*arguments: str) -> None:
    subprocess.run(
        ["uv", "run", "alembic", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _telemetry_columns() -> set[str]:
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'agent_runs'"
        )
        return {row[0] for row in cursor.fetchall()}


def test_provider_telemetry_migration_is_additive_and_nullable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260817_0013"' in source
    assert 'down_revision = "20260811_0012"' in source
    assert source.count("sa.Column(") == len(TELEMETRY_COLUMNS)
    assert source.count("nullable=True") == len(TELEMETRY_COLUMNS)
    assert 'op.drop_column("agent_runs", "model_revision")' in source


@pytest.mark.integration
def test_provider_telemetry_migration_round_trips_columns() -> None:
    try:
        _run_alembic("downgrade", "20260811_0012")
        assert TELEMETRY_COLUMNS.isdisjoint(_telemetry_columns())

        _run_alembic("upgrade", "20260817_0013")
        assert TELEMETRY_COLUMNS <= _telemetry_columns()
    finally:
        _run_alembic("upgrade", "head")
