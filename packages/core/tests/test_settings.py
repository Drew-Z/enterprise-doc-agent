from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from enterprise_doc_core.config import AppEnvironment, FoundationSettings


def test_nested_environment_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE__URL", "postgresql+psycopg://user:password@db/test")
    monkeypatch.setenv("REDIS__URL", "redis://:password@redis:6379/1")
    monkeypatch.setenv("OBJECT_STORE__ACCESS_KEY", "test-access")
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "test-secret")
    monkeypatch.setenv("OBJECT_STORE__ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("UPLOAD__MAX_FILE_SIZE_BYTES", "2147483648")
    monkeypatch.setenv("UPLOAD__PREFERRED_PART_SIZE_BYTES", "8388608")
    monkeypatch.setenv("UPLOAD__ENVELOPE_SAMPLE_BYTES", "8192")
    monkeypatch.setenv("UPLOAD__MAX_DOCX_ENTRIES", "2048")
    monkeypatch.setenv("OTEL__ENABLED", "true")
    monkeypatch.setenv("OTEL__SAMPLE_RATIO", "0.25")

    settings = FoundationSettings(_env_file=None)

    assert settings.app_env is AppEnvironment.TEST
    assert settings.database.url.get_secret_value().endswith("@db/test")
    assert settings.redis.url.get_secret_value().endswith("@redis:6379/1")
    assert settings.object_store.secret_key.get_secret_value() == "test-secret"
    assert settings.upload.max_file_size_bytes == 2 * 1024**3
    assert settings.upload.preferred_part_size_bytes == 8 * 1024**2
    assert settings.upload.envelope_sample_bytes == 8192
    assert settings.upload.max_docx_entries == 2048
    assert settings.otel.enabled is True
    assert settings.otel.sample_ratio == 0.25


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


def test_invalid_docx_envelope_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UPLOAD__MAX_DOCX_MEMBER_COMPRESSION_RATIO", "0.5")

    with pytest.raises(ValidationError):
        FoundationSettings(_env_file=None)
