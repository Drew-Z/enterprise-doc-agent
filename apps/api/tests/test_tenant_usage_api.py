from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.billing.contracts import TenantResourceUsage, UsageEventView, UsageSummary
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubUsageService:
    def __init__(self, summary: UsageSummary) -> None:
        self.value = summary
        self.calls: list[dict[str, Any]] = []

    async def summary(self, **kwargs: Any) -> UsageSummary:
        self.calls.append(kwargs)
        return self.value


def _app(principal: PrincipalContext, service: StubUsageService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        usage_service=service,  # type: ignore[arg-type]
    )


def _summary(tenant_id: UUID) -> UsageSummary:
    return UsageSummary(
        tenant_id=tenant_id,
        enabled=True,
        entitlement_status="active",
        plan_code="trial",
        version=2,
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 10, 1, tzinfo=UTC),
        provider_request_limit=10,
        provider_requests_used=3,
        provider_requests_reserved=1,
        provider_requests_remaining=6,
        cost_status="known",
        resources=TenantResourceUsage(
            storage_limit_bytes=1024,
            storage_used_bytes=320,
            storage_reserved_bytes=128,
            storage_remaining_bytes=576,
            seats_used=3,
            seat_limit=5,
            seats_remaining=2,
        ),
        recent_events=(
            UsageEventView(
                event_type="consume",
                quantity=1,
                operation_id=uuid4(),
                provider="provider",
                model="model",
                total_tokens=42,
                estimated_cost=None,
                currency=None,
                pricing_version=None,
                source="presales",
                occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
            ),
        ),
    )


async def test_tenant_usage_is_owner_only_and_serializes_unknown_cost() -> None:
    tenant_id = uuid4()
    owner = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    service = StubUsageService(_summary(tenant_id))
    app = _app(owner, service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/tenant-usage")
        response = await client.get("/api/tenant-usage", headers={"Authorization": "Bearer token"})
    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["tenantId"] == str(tenant_id)
    assert response.json()["entitlementStatus"] == "active"
    assert response.json()["providerRequestsRemaining"] == 6
    assert response.json()["recentEvents"][0]["estimatedCost"] is None
    assert response.json()["resources"] == {
        "storageLimitBytes": 1024,
        "storageUsedBytes": 320,
        "storageReservedBytes": 128,
        "storageRemainingBytes": 576,
        "seatsUsed": 3,
        "seatLimit": 5,
        "seatsRemaining": 2,
    }
    assert service.calls == [{"tenant_id": tenant_id}]


@pytest.mark.parametrize("status", ["legacy", "inactive"])
async def test_tenant_usage_distinguishes_legacy_and_inactive_entitlements(status) -> None:
    tenant_id = uuid4()
    owner = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    summary = replace(
        _summary(tenant_id),
        enabled=False,
        entitlement_status=status,
        plan_code=None,
        version=None,
        period_start=None,
        period_end=None,
        provider_request_limit=None,
        provider_requests_used=0,
        provider_requests_reserved=0,
        provider_requests_remaining=0 if status == "inactive" else None,
        cost_status="unknown",
        recent_events=(),
    )
    app = _app(owner, StubUsageService(summary))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tenant-usage", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["entitlementStatus"] == status
    assert body["enabled"] is False
    assert body["periodStart"] is None and body["periodEnd"] is None
    assert body["providerRequestsRemaining"] == (0 if status == "inactive" else None)
    assert body["recentEvents"] == []
    assert body["resources"]["seatsUsed"] == 3
    assert body["resources"]["storageRemainingBytes"] == 576


async def test_tenant_usage_preserves_decimal_cost_and_unconfigured_seat_limit() -> None:
    tenant_id = uuid4()
    owner = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    summary = _summary(tenant_id)
    summary = replace(
        summary,
        resources=replace(summary.resources, seat_limit=None, seats_remaining=None),
        recent_events=(
            replace(summary.recent_events[0], estimated_cost=Decimal("0.01234567"), currency="USD"),
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(owner, StubUsageService(summary))), base_url="http://test"
    ) as client:
        response = await client.get("/api/tenant-usage", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    body = response.json()
    assert body["resources"]["seatLimit"] is None
    assert body["resources"]["seatsRemaining"] is None
    assert body["recentEvents"][0]["estimatedCost"] == "0.01234567"


async def test_tenant_usage_rejects_members() -> None:
    tenant_id = uuid4()
    member = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="member")
    service = StubUsageService(_summary(tenant_id))
    app = _app(member, service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tenant-usage", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_usage_forbidden"
    assert service.calls == []


async def test_tenant_usage_reports_unavailable_service_without_leaking_data() -> None:
    tenant_id = uuid4()
    owner = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    app = _app(owner, StubUsageService(_summary(tenant_id)))
    app.state.usage_service = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tenant-usage", headers={"Authorization": "Bearer token"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "tenant_usage_unavailable"
    assert str(tenant_id) not in response.text
