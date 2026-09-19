from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import SecretStr

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.browser_sessions.errors import BrowserSessionInvalid

RANDOM_VALUE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SESSION_VALUE = re.compile(r"bss1_[A-Za-z0-9_-]{43}\Z")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_digest(credential: SecretStr) -> str:
    value = credential.get_secret_value()
    if not SESSION_VALUE.fullmatch(value):
        raise BrowserSessionInvalid()
    return digest(value)


def csrf_token(credential: SecretStr) -> str:
    session_digest(credential)
    return hmac.new(
        credential.get_secret_value().encode("ascii"), b"browser-session-csrf-v1", hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class LoginStart:
    state: SecretStr = field(repr=False)
    nonce: SecretStr = field(repr=False)
    verifier: SecretStr = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedLogin:
    attempt_id: UUID
    nonce_digest: str = field(repr=False)
    started_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserTenantChoice:
    tenant_id: UUID
    name: str
    user_id: UUID
    membership_id: UUID
    binding_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class BrowserSessionSnapshot:
    session_id: UUID
    generation: int
    identity: VerifiedAdmissionIdentity
    expires_at: datetime
    selected: BrowserTenantChoice | None

    @property
    def context_version(self) -> str:
        return f"{self.session_id.hex}.{self.generation}"


@dataclass(frozen=True, slots=True)
class IssuedBrowserSession:
    credential: SecretStr = field(repr=False)
    snapshot: BrowserSessionSnapshot
