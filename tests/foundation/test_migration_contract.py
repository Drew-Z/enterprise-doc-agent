from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/docker-compose.yml"
MIGRATIONS_PATH = ROOT / "packages/core/src/enterprise_doc_core/db/migrations/versions"
ALEMBIC_ENV_PATH = ROOT / "packages/core/src/enterprise_doc_core/db/migrations/env.py"


def test_compose_defines_only_m0_infrastructure_services() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"postgres", "redis", "minio", "minio-init"}
    assert set(compose["volumes"]) == {"postgres-data", "redis-data", "minio-data"}
    for service in ("postgres", "redis", "minio"):
        assert "healthcheck" in compose["services"][service]


def test_initial_migration_enables_vector_and_no_business_tables() -> None:
    migrations = list(MIGRATIONS_PATH.glob("*.py"))

    assert len(migrations) == 1
    migration = migrations[0].read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "DROP EXTENSION IF EXISTS vector" in migration
    assert "create_table" not in migration.lower()


def test_alembic_connection_uses_the_typed_connect_timeout() -> None:
    source = ALEMBIC_ENV_PATH.read_text(encoding="utf-8")

    assert 'connect_args={"connect_timeout": settings.connect_timeout_seconds}' in source


@pytest.mark.integration
def test_applied_migration_has_vector_and_no_business_tables() -> None:
    database_url = os.environ.get(
        "FOUNDATION_TEST_DATABASE_URL",
        "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
    )
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() == ("vector",)
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public' AND tablename <> 'alembic_version'
            ORDER BY tablename
            """
        )
        assert cursor.fetchall() == []
