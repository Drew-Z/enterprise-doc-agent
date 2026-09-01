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


def test_compose_keeps_m0_services_and_profiles_optional_stacks() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert set(compose["services"]) == {
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "prometheus",
        "grafana",
        "otel-collector",
    }
    assert compose["services"]["minio-init"]["profiles"] == ["init"]
    for service in ("prometheus", "grafana", "otel-collector"):
        assert compose["services"][service]["profiles"] == ["observability"]

    assert set(compose["volumes"]) == {
        "postgres-data",
        "redis-data",
        "minio-data",
        "prometheus-data",
        "grafana-data",
    }
    for service in ("postgres", "redis", "minio"):
        assert "healthcheck" in compose["services"][service]


def test_initial_migration_enables_vector_and_no_business_tables() -> None:
    migration_path = MIGRATIONS_PATH / "20260717_0001_enable_vector.py"

    assert migration_path.is_file()
    migration = migration_path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "DROP EXTENSION IF EXISTS vector" in migration
    assert "create_table" not in migration.lower()


def test_alembic_connection_uses_the_typed_connect_timeout() -> None:
    source = ALEMBIC_ENV_PATH.read_text(encoding="utf-8")

    assert 'connect_args={"connect_timeout": settings.connect_timeout_seconds}' in source


def test_audit_governance_migration_defines_retention_and_legal_hold_contracts() -> None:
    migration_path = MIGRATIONS_PATH / "20260825_0017_audit_governance.py"

    assert migration_path.is_file()
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision = "20260825_0017"' in migration
    assert 'down_revision = "20260825_0016"' in migration

    assert '"audit_retention_policies"' in migration
    assert 'name="audit_retention_days_valid"' in migration
    assert 'name="uq_audit_retention_policies_tenant_id"' in migration
    assert '"audit_legal_holds"' in migration
    assert 'name="audit_legal_hold_scope_valid"' in migration
    assert 'name="audit_legal_hold_expiry_valid"' in migration
    assert '"ix_audit_legal_holds_tenant_id_released_at_expires_at"' in migration
    assert '"ix_audit_legal_holds_tenant_id_resource"' in migration

    resource_index_drop = migration.index('op.drop_index("ix_audit_legal_holds_tenant_id_resource"')
    lifecycle_index_drop = migration.index(
        'op.drop_index(\n        "ix_audit_legal_holds_tenant_id_released_at_expires_at"'
    )
    legal_hold_drop = migration.index('op.drop_table("audit_legal_holds")')
    retention_policy_drop = migration.index('op.drop_table("audit_retention_policies")')

    assert resource_index_drop < legal_hold_drop
    assert lifecycle_index_drop < legal_hold_drop
    assert legal_hold_drop < retention_policy_drop


def test_audit_archive_migration_is_immutable_receipt_contract() -> None:
    migration_path = MIGRATIONS_PATH / "20260826_0018_audit_archive_batches.py"
    assert migration_path.is_file()
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision = "20260826_0018"' in migration
    assert 'down_revision = "20260825_0017"' in migration
    assert '"audit_archive_batches"' in migration
    assert 'name="uq_audit_archive_batches_tenant_fingerprint"' in migration
    assert '"content_sha256"' in migration
    assert '"archived_event_ids"' in migration
    assert 'op.drop_index("ix_audit_archive_batches_tenant_created_at"' in migration


def test_external_identity_binding_migration_is_explicit_subject_contract() -> None:
    migration_path = MIGRATIONS_PATH / "20260826_0019_external_identity_bindings.py"
    assert migration_path.is_file()
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision = "20260826_0019"' in migration
    assert 'down_revision = "20260826_0018"' in migration
    assert '"external_identity_bindings"' in migration
    assert 'name="uq_external_identity_bindings_tenant_issuer_subject"' in migration
    assert '"ix_external_identity_bindings_tenant_id_user_id_is_active"' in migration


def test_local_token_revocation_migration_is_tenant_scoped_and_expiring() -> None:
    migration_path = MIGRATIONS_PATH / "20260827_0020_local_token_revocations.py"
    assert migration_path.is_file()
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision = "20260827_0020"' in migration
    assert 'down_revision = "20260826_0019"' in migration
    assert '"local_token_revocations"' in migration
    assert 'name="uq_local_token_revocations_tenant_token"' in migration
    assert '"expires_at"' in migration
    assert 'op.drop_index("ix_local_token_revocations_expires_at"' in migration


@pytest.mark.integration
def test_applied_database_keeps_the_vector_extension() -> None:
    database_url = os.environ.get(
        "FOUNDATION_TEST_DATABASE_URL",
        "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
    )
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() == ("vector",)
