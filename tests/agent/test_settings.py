from __future__ import annotations

import pytest
from pydantic import ValidationError

from enterprise_doc_core.config import AppEnvironment, FoundationSettings, ModelProvider


def _set_non_local_foundation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", AppEnvironment.PRODUCTION.value)
    monkeypatch.setenv("DATABASE__URL", "postgresql+psycopg://user:password@db/enterprise")
    monkeypatch.setenv("OBJECT_STORE__ACCESS_KEY", "production-access")
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "production-secret")
    monkeypatch.setenv("MCP__SIGNING_SECRET", "mcp-production-signing-secret-at-least-32-bytes")


def test_local_and_test_may_use_deterministic_model_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", AppEnvironment.TEST.value)
    monkeypatch.setenv("DATABASE__URL", "postgresql+psycopg://user:password@db/test")
    monkeypatch.setenv("OBJECT_STORE__ACCESS_KEY", "test-access")
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "test-secret")

    settings = FoundationSettings(_env_file=None)

    assert settings.model.provider is ModelProvider.DETERMINISTIC
    assert settings.agent.strict_msgpack is True


def test_non_local_environment_rejects_deterministic_model_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_non_local_foundation(monkeypatch)

    with pytest.raises(ValidationError, match="deterministic model provider"):
        FoundationSettings(_env_file=None)


def test_openai_compatible_provider_requires_complete_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL__PROVIDER", ModelProvider.OPENAI_COMPATIBLE.value)
    monkeypatch.setenv("MODEL__BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MODEL__MODEL_NAME", "interview-model")
    monkeypatch.delenv("MODEL__API_KEY", raising=False)

    with pytest.raises(ValidationError, match="API key"):
        FoundationSettings(_env_file=None)


def test_strict_msgpack_cannot_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT__STRICT_MSGPACK", "false")

    with pytest.raises(ValidationError, match="strict msgpack"):
        FoundationSettings(_env_file=None)


def test_non_local_environment_rejects_development_mcp_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_non_local_foundation(monkeypatch)
    monkeypatch.setenv("MODEL__PROVIDER", ModelProvider.OPENAI_COMPATIBLE.value)
    monkeypatch.setenv("MODEL__BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MODEL__MODEL_NAME", "interview-model")
    monkeypatch.setenv("MODEL__API_KEY", "production-model-api-key")
    monkeypatch.setenv(
        "MCP__SIGNING_SECRET",
        "enterprise_doc_local_mcp_signing_secret_change_me_32_bytes",
    )

    with pytest.raises(ValidationError, match="development MCP signing secret"):
        FoundationSettings(_env_file=None)
