from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.product_contracts import ProductQuotaView


@dataclass(frozen=True, slots=True)
class PlatformEntitlementOperator:
    """Trusted platform capability; a tenant principal is not an operator."""

    operator_id: str
    reason: str


def require_entitlement_operator(value: object) -> PlatformEntitlementOperator:
    if not isinstance(value, PlatformEntitlementOperator):
        raise UsageError("entitlement_operator_forbidden")
    for item, maximum in ((value.operator_id, 128), (value.reason, 500)):
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
        ):
            raise UsageError("entitlement_operator_forbidden")
    return value


class ProductQuotaConfiguration(BaseModel):
    """Append missing product quotas to an existing period; never reset its counters."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    entitlement_id: UUID
    expected_version: int = Field(strict=True, ge=1, le=2**31 - 1)
    agent_task_limit: int = Field(strict=True, ge=0, le=2**63 - 1)
    document_bytes_limit: int = Field(strict=True, ge=0, le=2**63 - 1)


class EntitlementConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    entitlement_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=2**31 - 2)
    plan_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    period_start: AwareDatetime
    period_end: AwareDatetime
    provider_request_limit: int = Field(strict=True, ge=0, le=2**63 - 1)
    agent_task_limit: int = Field(default=0, strict=True, ge=0, le=2**63 - 1)
    document_bytes_limit: int = Field(default=0, strict=True, ge=0, le=2**63 - 1)

    @field_validator("period_start", "period_end")
    @classmethod
    def utc_period(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        return self


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    entitlement_id: UUID
    tenant_id: UUID
    plan_code: str
    version: int
    period_start: datetime
    period_end: datetime
    provider_request_limit: int | None
    provider_requests_used: int
    provider_requests_reserved: int
    created_at: datetime
    product_quotas: tuple[ProductQuotaView, ...] = ()


@dataclass(frozen=True, slots=True)
class EntitlementConfigurationResult:
    entitlement: EntitlementSnapshot
    replayed: bool
