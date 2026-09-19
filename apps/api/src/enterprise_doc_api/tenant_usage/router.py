from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.contracts import UsageEventView, UsageSummary
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.context import PrincipalContext


class UsageEventResponse(ApiModel):
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


class TenantResourceUsageResponse(ApiModel):
    storage_limit_bytes: int
    storage_used_bytes: int
    storage_reserved_bytes: int
    storage_remaining_bytes: int
    seats_used: int
    seat_limit: int | None
    seats_remaining: int | None


class TenantUsageResponse(ApiModel):
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
    cost_status: str = Field(pattern="^(known|unknown)$")
    recent_events: list[UsageEventResponse]
    resources: TenantResourceUsageResponse


router = APIRouter(prefix="/api/tenant-usage", tags=["tenant-usage"])
Principal = Annotated[PrincipalContext, Depends(get_current_principal)]


def _service(request: Request) -> EntitlementUsageService:
    service = cast(
        EntitlementUsageService | None,
        getattr(request.app.state, "usage_service", None),
    )
    if service is None:
        raise ApiError(
            status_code=503,
            code="tenant_usage_unavailable",
            message="Tenant usage is not configured.",
        )
    return service


def _owner(principal: PrincipalContext) -> UUID:
    if principal.role != "owner":
        raise ApiError(
            status_code=403,
            code="tenant_usage_forbidden",
            message="Only tenant owners can view usage.",
        )
    return UUID(principal.tenant_id)


def _event(event: UsageEventView) -> UsageEventResponse:
    return UsageEventResponse(
        event_type=event.event_type,
        quantity=event.quantity,
        operation_id=event.operation_id,
        provider=event.provider,
        model=event.model,
        total_tokens=event.total_tokens,
        estimated_cost=event.estimated_cost,
        currency=event.currency,
        pricing_version=event.pricing_version,
        source=event.source,
        occurred_at=event.occurred_at,
    )


def _response(summary: UsageSummary) -> TenantUsageResponse:
    return TenantUsageResponse(
        tenant_id=summary.tenant_id,
        enabled=summary.enabled,
        entitlement_status=summary.entitlement_status,
        plan_code=summary.plan_code,
        version=summary.version,
        period_start=summary.period_start,
        period_end=summary.period_end,
        provider_request_limit=summary.provider_request_limit,
        provider_requests_used=summary.provider_requests_used,
        provider_requests_reserved=summary.provider_requests_reserved,
        provider_requests_remaining=summary.provider_requests_remaining,
        cost_status=summary.cost_status,
        recent_events=[_event(item) for item in summary.recent_events],
        resources=TenantResourceUsageResponse(
            storage_limit_bytes=summary.resources.storage_limit_bytes,
            storage_used_bytes=summary.resources.storage_used_bytes,
            storage_reserved_bytes=summary.resources.storage_reserved_bytes,
            storage_remaining_bytes=summary.resources.storage_remaining_bytes,
            seats_used=summary.resources.seats_used,
            seat_limit=summary.resources.seat_limit,
            seats_remaining=summary.resources.seats_remaining,
        ),
    )


@router.get(
    "",
    response_model=TenantUsageResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def tenant_usage(
    request: Request, principal: Principal, response: Response
) -> TenantUsageResponse:
    response.headers["Cache-Control"] = "no-store"
    tenant_id = _owner(principal)
    try:
        summary = await _service(request).summary(tenant_id=tenant_id)
    except UsageError as error:
        raise ApiError(
            status_code=503,
            code=error.code,
            message="Tenant usage could not be read.",
        ) from error
    return _response(summary)
