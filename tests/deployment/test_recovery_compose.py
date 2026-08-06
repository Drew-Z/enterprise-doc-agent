from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
COMPOSE_FILE = ROOT / "infra" / "compose" / "recovery-postgres.yml"
DOCKERFILE = ROOT / "infra" / "docker" / "Dockerfile.recovery-postgres"


def test_recovery_pgvector_build_uses_the_pinned_postgres_toolchain() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM mirror.gcr.io/library/postgres@sha256:" in source
    assert "apk add --no-cache postgresql-pgvector" not in source
    assert "ARG PGVECTOR_VERSION=0.8.1" in source
    assert "ARG PGVECTOR_COMMIT=778dacf20c07caf904557a88705142631818d8cb" in source
    assert "https://github.com/pgvector/pgvector.git" in source
    assert 'fetch --depth 1 origin "${PGVECTOR_COMMIT}"' in source
    assert 'rev-parse HEAD)" = "${PGVECTOR_COMMIT}"' in source
    assert source.count("with_llvm=no") == 2


def test_recovery_dependencies_use_isolated_volumes_and_loopback_ports() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert compose["name"] == "enterprise-doc-recovery"
    assert set(services) == {
        "recovery-postgres",
        "recovery-redis",
        "recovery-minio",
        "recovery-minio-init",
    }
    assert services["recovery-postgres"]["ports"] == [
        "127.0.0.1:${RECOVERY_POSTGRES_PORT:-15432}:5432"
    ]
    assert services["recovery-redis"]["ports"] == ["127.0.0.1:${RECOVERY_REDIS_PORT:-16379}:6379"]
    assert services["recovery-redis"]["image"].startswith("redis:7.4-alpine@sha256:")
    assert services["recovery-minio"]["ports"] == [
        "127.0.0.1:${RECOVERY_MINIO_API_PORT:-19000}:9000",
        "127.0.0.1:${RECOVERY_MINIO_CONSOLE_PORT:-19001}:9001",
    ]
    assert services["recovery-postgres"]["volumes"] == [
        "enterprise-doc-recovery-postgres-data:/var/lib/postgresql/data"
    ]
    assert services["recovery-redis"]["volumes"] == ["enterprise-doc-recovery-redis-data:/data"]
    assert services["recovery-minio"]["volumes"] == ["enterprise-doc-recovery-minio-data:/data"]
    assert set(compose["volumes"]) == {
        "enterprise-doc-recovery-postgres-data",
        "enterprise-doc-recovery-redis-data",
        "enterprise-doc-recovery-minio-data",
    }
    assert services["recovery-minio-init"]["profiles"] == ["init"]
    assert "mc anonymous set private" in services["recovery-minio-init"]["entrypoint"][2]
