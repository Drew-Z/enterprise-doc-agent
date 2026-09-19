from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class ReservationResult:
    tenant_id: UUID
    operation_id: UUID
    quantity: int
    ledgered: bool
    replay: bool
    status: str
    reservation_id: UUID | None = None
    entitlement_id: UUID | None = None
    provider_request_limit: int | None = None
    provider_requests_used: int | None = None
    provider_requests_reserved: int | None = None


@dataclass(frozen=True)
class UsageEventView:
    event_type: str
    quantity: int
    operation_id: UUID
    provider: str | None
    model: str | None
    total_tokens: int | None
    estimated_cost: Decimal | None
    currency: str | None
    pricing_version: str | None
    source: str
    occurred_at: datetime


@dataclass(frozen=True)
class TenantResourceUsage:
    storage_limit_bytes: int
    storage_used_bytes: int
    storage_reserved_bytes: int
    storage_remaining_bytes: int
    seats_used: int
    seat_limit: int | None
    seats_remaining: int | None


@dataclass(frozen=True)
class UsageSummary:
    tenant_id: UUID
    enabled: bool
    entitlement_status: Literal["legacy", "active", "inactive"]
    plan_code: str | None
    version: int | None
    period_start: datetime | None
    period_end: datetime | None
    provider_request_limit: int | None
    provider_requests_used: int
    provider_requests_reserved: int
    provider_requests_remaining: int | None
    cost_status: str
    recent_events: tuple[UsageEventView, ...]
    resources: TenantResourceUsage
