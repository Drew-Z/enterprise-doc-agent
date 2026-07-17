from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get(
    "FOUNDATION_TEST_DATABASE_URL",
    "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
)
EXPECTED_TABLES = {
    "document_versions",
    "documents",
    "memberships",
    "tenants",
    "upload_parts",
    "upload_sessions",
    "users",
}


def _run_alembic(*arguments: str) -> None:
    subprocess.run(
        ["uv", "run", "alembic", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _business_tables() -> set[str]:
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename <> 'alembic_version'
            """
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.mark.integration
def test_m1_migration_downgrades_to_m0_and_reapplies() -> None:
    _run_alembic("upgrade", "head")
    assert _business_tables() == EXPECTED_TABLES

    _run_alembic("downgrade", "20260717_0001")
    assert _business_tables() == set()

    _run_alembic("upgrade", "head")
    assert _business_tables() == EXPECTED_TABLES


@pytest.mark.integration
def test_m1_database_has_required_unique_and_check_constraints() -> None:
    _run_alembic("upgrade", "head")
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
            """
        )
        constraints = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'upload_parts'
            """
        )
        upload_part_columns = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'upload_sessions'
            """
        )
        upload_session_columns = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT sequencename
            FROM pg_sequences
            WHERE schemaname = 'public'
            """
        )
        sequences = {row[0] for row in cursor.fetchall()}

    assert {
        "ck_tenants_quota_bytes_positive",
        "ck_tenants_storage_counters_non_negative",
        "ck_tenants_storage_within_quota",
        "ck_upload_sessions_expected_part_count_range",
        "ck_upload_sessions_part_size_positive",
        "ck_upload_sessions_reserved_bytes_non_negative",
        "ck_upload_sessions_size_bytes_positive",
        "ck_upload_sessions_status_valid",
        "uq_document_versions_document_id_version_number",
        "uq_document_versions_upload_session_id",
        "uq_upload_sessions_document_version_id",
        "uq_upload_sessions_tenant_id_idempotency_key",
    } <= constraints
    assert "document_version_id" in upload_session_columns
    assert {"observation_version", "observed_at"} <= upload_part_columns
    assert "upload_part_observation_version_seq" in sequences
