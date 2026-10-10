from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class UsageExportWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    start: AwareDatetime
    end: AwareDatetime
    limit_per_source: int = Field(default=5000, strict=True, ge=1, le=5000)

    @field_validator("start", "end", mode="before")
    @classmethod
    def explicit_datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("export dates must be ISO datetimes with a timezone")

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_window(self) -> Self:
        if not timedelta(0) < self.end - self.start <= timedelta(days=31):
            raise ValueError("export window must be positive and at most 31 days")
        return self


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    ledger: str
    id: UUID
    operation_id: UUID
    kind: str
    provider: str
    model: str | None
    channel_key: str
    route: str | None
    attempt_number: int | None
    state: str
    error_code: str | None
    http_status: int | None
    provider_request_id: str | None
    provider_response_id: str | None
    total_tokens: int | None
    estimated_cost: Decimal | None
    currency: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class BusinessEventRecord:
    ledger: str
    id: UUID
    operation_id: UUID
    reservation_id: UUID
    entitlement_id: UUID
    metric: str
    event_type: str
    quantity: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyPresalesRecord:
    operation_id: UUID
    state: str
    provider_request_count: int | None
    provider: str
    model: str | None
    provider_response_id: str | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    provider_records: int
    potentially_billable_records: int
    unresolved_records: int
    unknown_cost_records: int
    known_total_tokens: int
    missing_token_records: int
    # Each metric has its own unit. Released quantity is not a negative charge.
    business_quantities: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class UsageReconciliationExport:
    schema_version: str
    tenant_id: UUID
    window_start: datetime
    window_end_exclusive: datetime
    generated_at: datetime
    limit_per_source: int
    source_counts: dict[str, int]
    provider_calls: tuple[ProviderCallRecord, ...]
    business_events: tuple[BusinessEventRecord, ...]
    legacy_presales_attempts: tuple[LegacyPresalesRecord, ...]
    summary: ReconciliationSummary
    warnings: tuple[str, ...]
