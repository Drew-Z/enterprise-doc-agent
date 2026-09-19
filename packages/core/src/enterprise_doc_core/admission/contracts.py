from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from enterprise_doc_core.admission.errors import AdmissionDenied, AdmissionForbidden

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TOKEN = re.compile(r"adm1_[A-Za-z0-9_-]{43}\Z")


def normalized_email(value: str) -> str:
    result = value.strip().lower()
    if len(result) > 320 or not _EMAIL.fullmatch(result) or _has_control(result):
        raise ValueError("invalid email")
    return result


def exact_identity_value(value: str) -> str:
    if not value or len(value) > 512 or value != value.strip() or _has_control(value):
        raise ValueError("invalid identity value")
    return value


def normalized_tenant_name(value: str) -> str:
    result = value.strip()
    if not result or len(result) > 200 or _has_control(result):
        raise ValueError("invalid tenant name")
    return result


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class AdmissionGrantRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    recipient_email: str
    issuer: str
    expires_at: AwareDatetime
    quota_bytes: int = Field(strict=True, gt=0, le=2**63 - 1)
    seat_limit: int = Field(strict=True, gt=0, le=2**31 - 1)

    _email = field_validator("recipient_email")(normalized_email)
    _issuer = field_validator("issuer")(exact_identity_value)


@dataclass(frozen=True, slots=True)
class PlatformAdmissionOperator:
    """Construct only after platform authorization; tenant roles do not confer this capability."""

    operator_id: str
    reason: str


def require_operator(value: object) -> PlatformAdmissionOperator:
    if not isinstance(value, PlatformAdmissionOperator):
        raise AdmissionForbidden()
    for item, maximum in ((value.operator_id, 128), (value.reason, 500)):
        if not item.strip() or len(item) > maximum or _has_control(item):
            raise AdmissionForbidden()
        if "adm1_" in item:
            raise AdmissionForbidden()
    return value


@dataclass(frozen=True, slots=True)
class PreparedAdmissionCredential:
    grant_id: UUID
    token: SecretStr = field(repr=False)


def prepare_admission_credential() -> PreparedAdmissionCredential:
    return PreparedAdmissionCredential(uuid4(), SecretStr(f"adm1_{secrets.token_urlsafe(32)}"))


def credential_digest(token: SecretStr) -> str:
    value = token.get_secret_value()
    if not _TOKEN.fullmatch(value):
        raise AdmissionDenied()
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def value_digest(*values: str) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedAdmissionIdentity:
    """Trusted adapter output, never a public request body or a substitute for OIDC verification."""

    issuer: str = field(repr=False)
    subject: str = field(repr=False)
    email: str = field(repr=False)
    email_verified: bool


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    grant_id: UUID
    state: Literal["pending", "expired", "accepted", "revoked"]
    recipient_email: str
    issuer: str
    expires_at: datetime
    quota_bytes: int
    seat_limit: int
    issued_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    tenant_id: UUID | None


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    grant_id: UUID
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    binding_id: UUID
    entitlement_id: UUID
    tenant_slug: str
    accepted_at: datetime
    replayed: bool
