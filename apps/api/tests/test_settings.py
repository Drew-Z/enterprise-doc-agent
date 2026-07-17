from __future__ import annotations

import pytest

from enterprise_doc_api.config import ApiSettings


def test_api_settings_own_server_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API__HOST", "0.0.0.0")
    monkeypatch.setenv("API__PORT", "8123")
    monkeypatch.setenv("API__CORS_ORIGINS", '["https://console.example.com"]')

    settings = ApiSettings(_env_file=None)

    assert settings.api.host == "0.0.0.0"
    assert settings.api.port == 8123
    assert settings.api.cors_origins == ["https://console.example.com"]
