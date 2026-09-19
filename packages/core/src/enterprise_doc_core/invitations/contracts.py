from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from enterprise_doc_core.admission.contracts import normalized_email
from enterprise_doc_core.identity.seats import MembershipSeats
from enterprise_doc_core.invitations.errors import InvitationDenied

_TOKEN = re.compile(r"inv1_[A-Za-z0-9_-]{43}\Z")
InvitationState = Literal["pending", "expired", "accepted", "revoked"]


class InvitationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    ttl_seconds: int = Field(default=259200, strict=True, ge=60, le=604800)

    @field_validator("ttl_seconds", mode="before")
    @classmethod
    def environment_seconds(cls, value: object) -> object:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            return int(value)
        return value


class CreateInvitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    email: str
    operation_id: UUID

    _email = field_validator("email")(normalized_email)


class InvitationOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    expected_generation: int = Field(strict=True, ge=1, le=2**31 - 1)
    operation_id: UUID


def prepare_invitation_token() -> SecretStr:
    return SecretStr(f"inv1_{secrets.token_urlsafe(32)}")


def invitation_digest(token: SecretStr) -> str:
    value = token.get_secret_value()
    if not _TOKEN.fullmatch(value):
        raise InvitationDenied()
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class InvitationSnapshot:
    invitation_id: UUID
    email: str
    state: InvitationState
    generation: int
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InvitationList:
    items: tuple[InvitationSnapshot, ...]
    has_more: bool
    seats: MembershipSeats
    eligible: bool


@dataclass(frozen=True, slots=True)
class InvitationMutation:
    invitation: InvitationSnapshot
    replayed: bool
    token: SecretStr | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class InvitationPreview:
    tenant_name: str
    expires_at: datetime
    state: InvitationState


@dataclass(frozen=True, slots=True)
class InvitationReceipt:
    tenant_id: UUID
    tenant_name: str
    membership_id: UUID
    replayed: bool
