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
            "api_key": "provider-secret",
            "secret_key": SecretStr("object-secret"),
            "safe": "visible",
        },
    }

    sanitized = sanitize_log_value(value)

    assert sanitized == {
        "database_url": "**********",
        "nested": {
            "authorization": "**********",
            "api_key": "**********",
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


def test_json_formatter_redacts_upload_identifiers_and_signed_urls() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="api", environment="test"))
    logger = logging.getLogger("test-upload-log-redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    sentinels = {
        "upload-id-secret",
        "generic-upload-id-secret",
        "m1/uploads/object-key-secret",
        "private-file.pdf",
        "checksum-secret",
        "signed-url-secret",
    }

    logger.info(
        "upload_event",
        extra={
            "event_data": {
                "object_store_upload_id": "upload-id-secret",
                "upload_id": "generic-upload-id-secret",
                "object_key": "m1/uploads/object-key-secret",
                "filename": "private-file.pdf",
                "checksum_sha256": "checksum-secret",
                "url": (
                    "http://minio.test/object?X-Amz-Credential=value&"
                    "X-Amz-Signature=signed-url-secret"
                ),
                "nested": [{"declared_sha256": "checksum-secret"}],
                "safe_status": "active",
            }
        },
    )

    raw = stream.getvalue()
    payload = json.loads(raw)
    assert payload["safe_status"] == "active"
    assert payload["object_store_upload_id"] == "**********"
    assert payload["upload_id"] == "**********"
    assert payload["object_key"] == "**********"
    assert payload["filename"] == "**********"
    assert payload["checksum_sha256"] == "**********"
    assert payload["url"] == "**********"
    assert payload["nested"] == [{"declared_sha256": "**********"}]
    assert not any(sentinel in raw for sentinel in sentinels)


def test_json_formatter_does_not_render_message_arguments_or_unknown_objects() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="api", environment="test"))
    logger = logging.getLogger("test-dynamic-log-redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    signed_url = "http://minio.test/object?X-Amz-Signature=message-secret"

    logger.error("upload failed: %s", signed_url)
    logger.error(
        "upload_error",
        extra={"event_data": {"error": RuntimeError("exception-secret")}},
    )

    raw = stream.getvalue()
    payloads = [json.loads(line) for line in raw.splitlines()]
    assert payloads[0]["event"] == "**********"
    assert payloads[1]["event"] == "upload_error"
    assert payloads[1]["error"] == "<RuntimeError>"
    assert "message-secret" not in raw
    assert "exception-secret" not in raw
