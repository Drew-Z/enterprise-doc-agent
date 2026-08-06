from __future__ import annotations

from typing import Any

from pydantic import SecretStr

import enterprise_doc_core.object_store.client as object_store_client
from enterprise_doc_core.config import ObjectStoreSettings


def test_create_s3_client_passes_optional_session_token(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(service: str, **kwargs: Any) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(object_store_client.boto3, "client", fake_client)
    settings = ObjectStoreSettings(
        access_key=SecretStr("temporary-access"),
        secret_key=SecretStr("temporary-secret"),
        session_token=SecretStr("temporary-session"),
    )

    object_store_client.create_s3_client(settings, endpoint_url=settings.endpoint)

    assert captured["service"] == "s3"
    assert captured["aws_access_key_id"] == "temporary-access"
    assert captured["aws_secret_access_key"] == "temporary-secret"
    assert captured["aws_session_token"] == "temporary-session"


def test_create_s3_client_keeps_session_token_optional(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_client(_service: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(object_store_client.boto3, "client", fake_client)

    object_store_client.create_s3_client(
        ObjectStoreSettings(),
        endpoint_url="http://127.0.0.1:9000",
    )

    assert captured["aws_session_token"] is None
