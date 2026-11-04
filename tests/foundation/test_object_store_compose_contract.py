from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/docker-compose.yml"


def test_minio_images_are_immutable_and_init_keeps_buckets_private() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    server = compose["services"]["minio"]
    init = compose["services"]["minio-init"]

    assert server["build"]["target"] == "minio"
    assert init["build"]["target"] == "mc"
    assert init["depends_on"]["minio"]["condition"] == "service_healthy"
    command = "\n".join(init["entrypoint"])
    assert "mc anonymous set private local/documents" in command
    assert "mc anonymous set private local/artifacts" in command
    assert "mc anonymous get local/documents" in command
    assert "mc anonymous get local/artifacts" in command


def test_community_minio_cors_is_restricted_to_configured_web_origins() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    value = compose["services"]["minio"]["environment"]["MINIO_API_CORS_ALLOW_ORIGIN"]

    assert "http://127.0.0.1:5173" in value
    assert "http://localhost:5173" in value
    assert "*" not in value


@pytest.mark.parametrize("filename", ["docker-compose.yml", "recovery-postgres.yml"])
def test_object_store_builds_verify_official_inputs_instead_of_reusing_mutable_tags(
    filename: str,
) -> None:
    compose = yaml.safe_load((ROOT / "infra/compose" / filename).read_text(encoding="utf-8"))
    services = compose["services"]
    prefix = "recovery-" if filename.startswith("recovery") else ""
    for name, target in (("minio", "minio"), ("minio-init", "mc")):
        service = services[prefix + name]
        assert service["pull_policy"] == "build"
        assert service["platform"] == "linux/amd64"
        assert service["build"] == {
            "context": "../docker",
            "dockerfile": "Dockerfile.object-store",
            "target": target,
        }
    source = (ROOT / "infra/docker/Dockerfile.object-store").read_text(encoding="utf-8")
    assert "FROM alpine:3.24.2@sha256:" in source
    assert (
        "--checksum=sha256:7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f"
        in source
    )
    assert (
        "--checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891"
        in source
    )
    assert "https://github.com/minio/minio/releases/download/RELEASE." in source
    assert "https://github.com/minio/mc/releases/download/RELEASE." in source
