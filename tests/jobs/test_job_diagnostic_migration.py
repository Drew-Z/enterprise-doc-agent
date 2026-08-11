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
    / "20260811_0012_job_attempt_diagnostics.py"
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


def test_job_attempt_diagnostic_migration_is_additive_and_nullable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260811_0012"' in source
    assert 'down_revision = "20260804_0011"' in source
    assert "op.add_column(" in source
    assert '"job_attempts"' in source
    assert 'sa.Column("diagnostic_code", sa.String(length=100), nullable=True)' in source
    assert 'op.drop_column("job_attempts", "diagnostic_code")' in source


@pytest.mark.integration
def test_job_attempt_diagnostic_migration_round_trips_nullable_column() -> None:
    try:
        _run_alembic("downgrade", "20260804_0011")
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'job_attempts' "
                "AND column_name = 'diagnostic_code'"
            )
            assert cursor.fetchone() is None

        _run_alembic("upgrade", "20260811_0012")
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, character_maximum_length, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'job_attempts' "
                "AND column_name = 'diagnostic_code'"
            )
            assert cursor.fetchone() == ("character varying", 100, "YES")
    finally:
        _run_alembic("upgrade", "head")
