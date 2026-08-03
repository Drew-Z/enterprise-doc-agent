from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from enterprise_doc_core.config import (
    AppEnvironment,
    FoundationSettings,
    ModelSettings,
    ObjectStoreChecksumMode,
)


def test_nested_environment_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE__URL", "postgresql+psycopg://user:password@db/test")
    monkeypatch.setenv("DATABASE__POOL_SIZE", "4")
    monkeypatch.setenv("DATABASE__MAX_OVERFLOW", "1")
    monkeypatch.setenv("DATABASE__POOL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("DATABASE__POOL_RECYCLE_SECONDS", "480")
    monkeypatch.setenv("REDIS__URL", "redis://:password@redis:6379/1")
    monkeypatch.setenv("OBJECT_STORE__ACCESS_KEY", "test-access")
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "test-secret")
    monkeypatch.setenv("OBJECT_STORE__ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("OBJECT_STORE__MULTIPART_CHECKSUM_MODE", "readback_sha256")
    monkeypatch.setenv("UPLOAD__MAX_FILE_SIZE_BYTES", "2147483648")
    monkeypatch.setenv("UPLOAD__PREFERRED_PART_SIZE_BYTES", "8388608")
    monkeypatch.setenv("UPLOAD__ENVELOPE_SAMPLE_BYTES", "8192")
    monkeypatch.setenv("UPLOAD__MAX_DOCX_ENTRIES", "2048")
    monkeypatch.setenv("UPLOAD__CLEANUP_BATCH_SIZE", "25")
    monkeypatch.setenv("UPLOAD__CLEANUP_CLAIM_TTL_SECONDS", "120")
    monkeypatch.setenv("UPLOAD__CLEANUP_COMPLETING_GRACE_SECONDS", "600")
    monkeypatch.setenv("OTEL__ENABLED", "true")
    monkeypatch.setenv("OTEL__SAMPLE_RATIO", "0.25")
    monkeypatch.setenv("EMBEDDING__QUERY_INSTRUCTION", "Retrieve enterprise evidence")

    settings = FoundationSettings(_env_file=None)

    assert settings.app_env is AppEnvironment.TEST
    assert settings.database.url.get_secret_value().endswith("@db/test")
    assert settings.database.pool_size == 4
    assert settings.database.max_overflow == 1
    assert settings.database.pool_timeout_seconds == 7
    assert settings.database.pool_recycle_seconds == 480
    assert settings.redis.url.get_secret_value().endswith("@redis:6379/1")
    assert settings.object_store.secret_key.get_secret_value() == "test-secret"
    assert settings.object_store.multipart_checksum_mode is ObjectStoreChecksumMode.READBACK_SHA256
    assert settings.upload.max_file_size_bytes == 2 * 1024**3
    assert settings.upload.preferred_part_size_bytes == 8 * 1024**2
    assert settings.upload.envelope_sample_bytes == 8192
    assert settings.upload.max_docx_entries == 2048
    assert settings.upload.cleanup_batch_size == 25
    assert settings.upload.cleanup_claim_ttl_seconds == 120
    assert settings.upload.cleanup_completing_grace_seconds == 600
    assert settings.otel.enabled is True
    assert settings.otel.sample_ratio == 0.25
    assert settings.embedding.query_instruction == "Retrieve enterprise evidence"


def test_non_local_environment_rejects_local_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DATABASE__URL", raising=False)
    monkeypatch.delenv("OBJECT_STORE__ACCESS_KEY", raising=False)
    monkeypatch.delenv("OBJECT_STORE__SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="development credentials"):
        FoundationSettings(_env_file=None)


def test_secrets_are_redacted_from_repr_and_safe_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "top-secret-value")
    settings = FoundationSettings(_env_file=None)

    assert isinstance(settings.object_store.secret_key, SecretStr)
    assert "top-secret-value" not in repr(settings)
    assert "top-secret-value" not in str(settings.safe_dump())
    assert settings.safe_dump()["object_store"]["secret_key"] == "**********"


def test_invalid_sample_ratio_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL__SAMPLE_RATIO", "1.1")

    with pytest.raises(ValidationError):
        FoundationSettings(_env_file=None)


def test_embedding_dimensions_match_the_fixed_vector_index() -> None:
    assert ModelSettings().embedding_dimension == 1024
    assert FoundationSettings(_env_file=None).embedding.dimension == 1024
    with pytest.raises(ValidationError):
        ModelSettings(embedding_dimension=1536)


def test_embedding_query_instruction_is_bounded() -> None:
    assert FoundationSettings(_env_file=None).embedding.query_instruction.startswith(
        "Given a user question"
    )
    with pytest.raises(ValidationError):
        FoundationSettings(
            _env_file=None,
            embedding={"query_instruction": "x" * 501},
        )


def test_model_route_deadline_is_optional_and_bounded() -> None:
    assert ModelSettings().route_deadline_seconds is None
    assert ModelSettings(route_deadline_seconds=12.5).route_deadline_seconds == 12.5
    with pytest.raises(ValidationError):
        ModelSettings(route_deadline_seconds=0)
    with pytest.raises(ValidationError):
        ModelSettings(route_deadline_seconds=601)


def test_invalid_docx_envelope_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UPLOAD__MAX_DOCX_MEMBER_COMPRESSION_RATIO", "0.5")

    with pytest.raises(ValidationError):
        FoundationSettings(_env_file=None)


def test_fault_injection_is_rejected_outside_local_test() -> None:
    with pytest.raises(ValidationError, match="fault injection is forbidden"):
        FoundationSettings(
            _env_file=None,
            app_env="production",
            database={"url": "postgresql+psycopg://user:password@database/app"},
            object_store={
                "access_key": "production-access",
                "secret_key": "production-secret",
            },
            model={
                "provider": "openai_compatible",
                "base_url": "https://model.example/v1",
                "api_key": "model-secret",
                "model_name": "production-model",
            },
            embedding={
                "provider": "openai_compatible",
                "base_url": "https://embedding.example/v1",
                "api_key": "embedding-secret",
                "model_name": "Qwen/Qwen3-Embedding-4B",
            },
            mcp={"signing_secret": "production-signing-secret-at-least-32-bytes"},
            fault_injection={
                "enabled": True,
                "target": "handler",
                "mode": "retryable",
            },
        )
