from __future__ import annotations

import json
import logging
from io import StringIO

from pydantic import SecretStr

from enterprise_doc_core.logging import JsonFormatter, sanitize_log_value


def test_sanitize_log_value_redacts_nested_sensitive_fields() -> None:
    value = {
        "database_url": "postgresql://user:password@db/app",
        "nested": {
            "authorization": "Bearer token",
            "secret_key": SecretStr("object-secret"),
            "safe": "visible",
        },
    }

    sanitized = sanitize_log_value(value)

    assert sanitized == {
        "database_url": "**********",
        "nested": {
            "authorization": "**********",
            "secret_key": "**********",
            "safe": "visible",
        },
    }


def test_json_formatter_emits_stable_fields_without_secret_values() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="api", environment="test"))
    logger = logging.getLogger("test-json-formatter")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "request_completed",
        extra={
            "event_data": {
                "request_id": "request-1",
                "correlation_id": "correlation-1",
                "duration_ms": 12.5,
                "password": "do-not-log",
            }
        },
    )

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["service"] == "api"
    assert payload["environment"] == "test"
    assert payload["event"] == "request_completed"
    assert payload["request_id"] == "request-1"
    assert payload["correlation_id"] == "correlation-1"
    assert payload["password"] == "**********"
    assert "do-not-log" not in stream.getvalue()
