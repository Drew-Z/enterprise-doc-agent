from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from enterprise_doc_core.context import get_request_context

REDACTED = "**********"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "checksum",
    "database_url",
    "dsn",
    "filename",
    "object_key",
    "object_store_upload_id",
    "upload_id",
    "password",
    "redis_url",
    "secret",
    "sha256",
    "signature",
    "token",
)
_SENSITIVE_STRING_MARKERS = (
    "awsaccesskeyid=",
    "x-amz-credential=",
    "x-amz-security-token=",
    "x-amz-signature=",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def sanitize_log_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_log_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, str):
        normalized = value.lower()
        if any(marker in normalized for marker in _SENSITIVE_STRING_MARKERS):
            return REDACTED
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _sanitize_log_event(record: logging.LogRecord) -> str:
    if not isinstance(record.msg, str) or record.args:
        return REDACTED
    sanitized = sanitize_log_value(record.msg)
    return sanitized if isinstance(sanitized, str) else REDACTED


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        context = get_request_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "event": _sanitize_log_event(record),
        }
        if context is not None:
            payload["request_id"] = context.request_id
            payload["correlation_id"] = context.correlation_id
            if context.principal is not None:
                payload["tenant_id"] = context.principal.tenant_id
                payload["actor_id"] = context.principal.actor_id

        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, Mapping):
            payload.update(sanitize_log_value(event_data))
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(*, service: str, environment: str, level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service=service, environment=environment))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
