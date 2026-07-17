from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/docker-compose.yml"
MINIO_IMAGE = (
    "minio/minio:RELEASE.2025-09-07T16-13-09Z@"
    "sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)
MC_IMAGE = (
    "minio/mc:RELEASE.2025-08-13T08-35-41Z@"
    "sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
)


def test_minio_images_are_immutable_and_init_keeps_buckets_private() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    server = compose["services"]["minio"]
    init = compose["services"]["minio-init"]

    assert server["image"] == MINIO_IMAGE
    assert init["image"] == MC_IMAGE
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
