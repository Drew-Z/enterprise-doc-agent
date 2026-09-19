from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.admission.models import TenantAdmissionGrant, TenantInitialEntitlement
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity.models import Membership, Tenant, User

pytestmark = pytest.mark.integration


@pytest.fixture
async def resource_database(billing_database):
    factory, (tenant_id, other_id) = billing_database
    now = datetime(2026, 9, 15, tzinfo=UTC)
    user_ids = [uuid4() for _ in range(4)]
    grant_id = uuid4()
    try:
        async with factory.begin() as session:
            await session.execute(
                update(Tenant)
                .where(Tenant.id == tenant_id)
                .values(used_storage_bytes=320, reserved_storage_bytes=128)
            )
            session.add_all(
                User(id=user_id, email=f"resource-{user_id.hex}@example.test", is_active=index != 2)
                for index, user_id in enumerate(user_ids)
            )
            session.add(
                TenantAdmissionGrant(
                    id=grant_id,
                    token_digest=uuid4().hex + uuid4().hex,
                    recipient_email=f"resource-{user_ids[0].hex}@example.test",
                    issuer="https://resource.example.test",
                    quota_bytes=8192,
                    seat_limit=5,
                    issued_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            await session.flush()
            session.add(
                TenantInitialEntitlement(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    grant_id=grant_id,
                    quota_bytes=8192,
                    seat_limit=5,
                    created_at=now,
                )
            )
            session.add_all(
                Membership(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role="owner" if index == 0 else "member",
                    is_active=index != 3,
                )
                for index, user_id in enumerate(user_ids)
            )
            session.add(Membership(tenant_id=other_id, user_id=user_ids[0], role="owner"))
        yield factory, tenant_id, other_id, now
    finally:
        async with factory.begin() as session:
            await session.execute(
                delete(TenantInitialEntitlement).where(
                    TenantInitialEntitlement.grant_id == grant_id
                )
            )
            await session.execute(
                delete(TenantAdmissionGrant).where(TenantAdmissionGrant.id == grant_id)
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.mark.parametrize("status", ["legacy", "active", "inactive"])
async def test_summary_reports_authoritative_resources_in_every_period_state(
    resource_database, status
) -> None:
    factory, tenant_id, other_id, now = resource_database
    if status != "legacy":
        async with factory.begin() as session:
            session.add(
                TenantEntitlement(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    plan_code="trial",
                    version=1,
                    period_start=now - timedelta(days=1),
                    period_end=now + timedelta(days=1) if status == "active" else now,
                    provider_request_limit=10,
                    created_at=now,
                    updated_at=now,
                )
            )
    service = EntitlementUsageService(session_factory=factory, clock=lambda: now)
    summary = await service.summary(tenant_id=tenant_id)
    assert summary.entitlement_status == status
    resources = summary.resources
    # Current Tenant quota wins over the admission-time storage snapshot.
    assert resources.storage_limit_bytes == 1024
    assert resources.storage_used_bytes == 320
    assert resources.storage_reserved_bytes == 128
    assert resources.storage_remaining_bytes == 576
    # Match membership capacity enforcement, including active membership on an inactive User.
    assert resources.seats_used == 3
    assert resources.seat_limit == 5
    assert resources.seats_remaining == 2

    other = (await service.summary(tenant_id=other_id)).resources
    assert other.storage_used_bytes == other.storage_reserved_bytes == 0
    assert other.storage_remaining_bytes == 1024
    assert other.seats_used == 1
    assert other.seat_limit is None and other.seats_remaining is None


async def test_summary_reports_capacity_reached_without_negative_remaining(
    resource_database,
) -> None:
    factory, tenant_id, _, now = resource_database
    async with factory.begin() as session:
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(used_storage_bytes=768, reserved_storage_bytes=256)
        )
        await session.execute(
            update(TenantInitialEntitlement)
            .where(TenantInitialEntitlement.tenant_id == tenant_id)
            .values(seat_limit=3)
        )
    summary = await EntitlementUsageService(session_factory=factory, clock=lambda: now).summary(
        tenant_id=tenant_id
    )
    assert summary.resources.storage_remaining_bytes == 0
    assert summary.resources.seats_remaining == 0


@pytest.mark.parametrize("role", ["owner", "member"])
async def test_http_resources_follow_the_authenticated_tenant(resource_database, role) -> None:
    factory, tenant_id, other_id, now = resource_database
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role=role)

    class TestIdentityResolver:
        async def resolve(self, _: str) -> PrincipalContext:
            return principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=TestIdentityResolver(),
        usage_service=EntitlementUsageService(session_factory=factory, clock=lambda: now),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/tenant-usage?tenantId={other_id}", headers={"Authorization": "Bearer test-token"}
        )
    if role == "member":
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "tenant_usage_forbidden"
        assert "resources" not in response.json()
    else:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["tenantId"] == str(tenant_id)
        assert response.json()["resources"]["storageUsedBytes"] == 320
        assert response.json()["resources"]["seatsUsed"] == 3
