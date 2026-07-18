from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class ToolCapability(StrEnum):
    READ_EVIDENCE = "read_evidence"
    CREATE_DRAFT = "create_draft"
    READ_ARTIFACT = "read_artifact"
    PUBLISH = "publish"


class ExecutionContextError(ValueError):
    code = "execution_context_invalid"


class ExecutionContextMalformed(ExecutionContextError):
    code = "execution_context_malformed"


class ExecutionContextInvalidSignature(ExecutionContextError):
    code = "execution_context_invalid_signature"


class ExecutionContextExpired(ExecutionContextError):
    code = "execution_context_expired"


class ExecutionContextNotYetValid(ExecutionContextError):
    code = "execution_context_not_yet_valid"


class ExecutionContextVersionUnsupported(ExecutionContextError):
    code = "execution_context_version_unsupported"


class SignedExecutionContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )

    version: Literal[1] = 1
    tenant_id: UUID
    actor_id: UUID
    run_id: UUID
    execution_id: UUID
    capabilities: tuple[ToolCapability, ...] = Field(min_length=1, max_length=8)
    target_document_version_id: UUID
    approval_request_id: UUID | None = None
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def validate_temporal_and_capability_shape(self) -> SignedExecutionContext:
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("execution context timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if ToolCapability.PUBLISH in self.capabilities and self.approval_request_id is None:
            raise ValueError("publish capability requires an approval binding")
        return self

    def allows(self, capability: ToolCapability) -> bool:
        return capability in self.capabilities


def sign_execution_context(
    context: SignedExecutionContext,
    secret: SecretStr | str | bytes,
) -> str:
    secret_bytes = _secret_bytes(secret)
    payload = _canonical_payload(context)
    signature = hmac.new(secret_bytes, payload, hashlib.sha256).digest()
    return f"v1.{_encode(payload)}.{_encode(signature)}"


def verify_execution_context(
    token: str,
    secret: SecretStr | str | bytes,
    *,
    now: datetime | None = None,
    clock_skew_seconds: int = 5,
) -> SignedExecutionContext:
    if not isinstance(token, str) or len(token) > 16_384:
        raise ExecutionContextMalformed()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        if parts and parts[0] != "v1":
            raise ExecutionContextVersionUnsupported()
        raise ExecutionContextMalformed()
    try:
        payload = _decode(parts[1])
        supplied_signature = _decode(parts[2])
    except ValueError as error:
        raise ExecutionContextMalformed() from error
    expected_signature = hmac.new(_secret_bytes(secret), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ExecutionContextInvalidSignature()
    try:
        context = _CONTEXT_ADAPTER.validate_json(payload, strict=True)
    except ValidationError as error:
        raise ExecutionContextMalformed() from error
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if current < context.issued_at - timedelta(seconds=clock_skew_seconds):
        raise ExecutionContextNotYetValid()
    if current >= context.expires_at:
        raise ExecutionContextExpired()
    return context


def _secret_bytes(secret: SecretStr | str | bytes) -> bytes:
    if isinstance(secret, SecretStr):
        value = secret.get_secret_value()
    elif isinstance(secret, str):
        value = secret
    elif isinstance(secret, bytes):
        return _validate_secret(value=secret)
    else:
        raise TypeError("secret must be SecretStr, str, or bytes")
    return _validate_secret(value=value.encode("utf-8"))


def _validate_secret(*, value: bytes) -> bytes:
    if len(value) < 32:
        raise ValueError("execution context secret must be at least 32 bytes")
    return value


def _canonical_payload(context: SignedExecutionContext) -> bytes:
    return json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url value")
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


_CONTEXT_ADAPTER: TypeAdapter[SignedExecutionContext] = TypeAdapter(SignedExecutionContext)


__all__ = [
    "ExecutionContextError",
    "ExecutionContextExpired",
    "ExecutionContextInvalidSignature",
    "ExecutionContextMalformed",
    "ExecutionContextNotYetValid",
    "ExecutionContextVersionUnsupported",
    "SignedExecutionContext",
    "ToolCapability",
    "sign_execution_context",
    "verify_execution_context",
]
