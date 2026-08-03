from __future__ import annotations

import pytest
from pydantic import ValidationError

from enterprise_doc_api.config import ApiSettings


def test_api_settings_own_server_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API__HOST", "0.0.0.0")
    monkeypatch.setenv("API__PORT", "8123")
    monkeypatch.setenv("API__CORS_ORIGINS", '["https://console.example.com"]')

    settings = ApiSettings(_env_file=None)

    assert settings.api.host == "0.0.0.0"
    assert settings.api.port == 8123
    assert settings.api.cors_origins == ["https://console.example.com"]


def test_api_auth_settings_are_secret_aware_and_reject_local_key_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = ApiSettings(_env_file=None)

    assert local.auth.signing_key.get_secret_value()
    assert local.safe_dump()["auth"]["signing_key"] == "**********"

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv(
        "DATABASE__URL",
        "postgresql+psycopg://service:real-password@database:5432/app",
    )
    monkeypatch.setenv("OBJECT_STORE__ACCESS_KEY", "staging-access")
    monkeypatch.setenv("OBJECT_STORE__SECRET_KEY", "staging-secret")
    monkeypatch.setenv("MODEL__PROVIDER", "openai_compatible")
    monkeypatch.setenv("MODEL__BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MODEL__API_KEY", "staging-model-api-key")
    monkeypatch.setenv("MODEL__MODEL_NAME", "staging-model")
    monkeypatch.setenv("EMBEDDING__PROVIDER", "openai_compatible")
    monkeypatch.setenv("EMBEDDING__BASE_URL", "https://embedding.example.test/v1")
    monkeypatch.setenv("EMBEDDING__API_KEY", "staging-embedding-api-key")
    monkeypatch.setenv("EMBEDDING__MODEL_NAME", "Qwen/Qwen3-Embedding-4B")
    monkeypatch.setenv("MCP__SIGNING_SECRET", "staging-mcp-signing-secret-at-least-32-bytes")
    with pytest.raises(ValidationError, match="development JWT signing key"):
        ApiSettings(_env_file=None)
