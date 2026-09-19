from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from enterprise_doc_core.billing import EntitlementUsageService, UsageError
from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.identity.models import Tenant

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "periods", [[(-2, -1)], [(1, 2)], [(-2, -1), (1, 2)]], ids=["expired", "scheduled", "gap"]
)
async def test_configured_tenant_without_active_period_cannot_fall_back_to_legacy(
    billing_database, periods
) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    async with factory.begin() as session:
        for version, (start, end) in enumerate(periods, 1):
            session.add(
                TenantEntitlement(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    plan_code="trial",
                    version=version,
                    period_start=now + timedelta(days=start),
                    period_end=now + timedelta(days=end),
                    provider_request_limit=10,
                    created_at=now,
                    updated_at=now,
                )
            )
    service = EntitlementUsageService(session_factory=factory, clock=lambda: now)
    with pytest.raises(UsageError, match=r"^usage_entitlement_inactive$"):
        await service.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
    summary = await service.summary(tenant_id=tenant_id)
    assert summary.entitlement_status == "inactive"
    assert not summary.enabled
    assert summary.provider_requests_remaining == 0
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageReservation)
                .where(UsageReservation.tenant_id == tenant_id)
            )
            == 0
        )


@pytest.mark.parametrize("operation", ["reserve", "summary"])
async def test_period_is_checked_after_waiting_for_tenant_lock(billing_database, operation) -> None:
    factory, (tenant_id, _) = billing_database
    boundary = datetime(2026, 9, 15, tzinfo=UTC)
    clock = [boundary - timedelta(seconds=1)]
    async with factory.begin() as session:
        session.add(
            TenantEntitlement(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_code="trial",
                version=1,
                period_start=boundary - timedelta(days=1),
                period_end=boundary,
                provider_request_limit=10,
                created_at=clock[0],
                updated_at=clock[0],
            )
        )
    service = EntitlementUsageService(session_factory=factory, clock=lambda: clock[0])
    pending = None
    try:
        async with factory.begin() as blocker:
            await blocker.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
            pending = asyncio.create_task(
                service.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
                if operation == "reserve"
                else service.summary(tenant_id=tenant_id)
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), timeout=0.2)
            clock[0] = boundary
        if operation == "reserve":
            with pytest.raises(UsageError, match=r"^usage_entitlement_inactive$"):
                await pending
        else:
            assert (await pending).entitlement_status == "inactive"
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("operation", ["reserve", "summary"])
async def test_database_failure_before_commit_is_stable_and_rolls_back(
    billing_database, operation
) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    async with factory.begin() as session:
        session.add(
            TenantEntitlement(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_code="trial",
                version=1,
                period_start=now,
                period_end=now + timedelta(days=1),
                provider_request_limit=2,
                created_at=now,
                updated_at=now,
            )
        )

    class FailingTransaction:
        @asynccontextmanager
        async def begin(self):
            async with factory.begin() as session:
                yield session
                # Real PostgreSQL failure after the ledger work and before commit.
                await session.execute(text("SELECT 1 / 0"))

    service = EntitlementUsageService(session_factory=FailingTransaction(), clock=lambda: now)
    with pytest.raises(UsageError, match=r"^usage_store_unavailable$"):
        if operation == "reserve":
            await service.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
        else:
            await service.summary(tenant_id=tenant_id)
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageReservation)
                .where(
                    UsageReservation.tenant_id == tenant_id,
                )
            )
            == 0
        )
        entitlement = await session.scalar(
            select(TenantEntitlement).where(
                TenantEntitlement.tenant_id == tenant_id,
            )
        )
        assert entitlement.provider_requests_reserved == entitlement.provider_requests_used == 0


async def test_legacy_and_original_period_settlement_remain_compatible(billing_database) -> None:
    factory, (tenant_id, legacy_id) = billing_database
    boundary = datetime(2026, 9, 15, tzinfo=UTC)
    clock = [boundary - timedelta(seconds=1)]
    service = EntitlementUsageService(session_factory=factory, clock=lambda: clock[0])
    legacy = await service.reserve_provider_request(tenant_id=legacy_id, operation_id=uuid4())
    assert not legacy.ledgered and legacy.status == "legacy"
    legacy_summary = await service.summary(tenant_id=legacy_id)
    assert legacy_summary.entitlement_status == "legacy"
    assert legacy_summary.provider_requests_remaining is None
    async with factory.begin() as session:
        for version, start, end in (
            (1, boundary - timedelta(days=1), boundary),
            (2, boundary + timedelta(seconds=1), boundary + timedelta(days=1)),
        ):
            session.add(
                TenantEntitlement(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    plan_code="trial",
                    version=version,
                    period_start=start,
                    period_end=end,
                    provider_request_limit=2,
                    created_at=clock[0],
                    updated_at=clock[0],
                )
            )
    operation_id = uuid4()
    reserved = await service.reserve_provider_request(
        tenant_id=tenant_id, operation_id=operation_id
    )
    clock[0] = boundary
    settled = await service.settle_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert settled.entitlement_id == reserved.entitlement_id
    assert settled.provider_requests_used == 1 and settled.provider_requests_reserved == 0
    with pytest.raises(UsageError, match="usage_entitlement_inactive"):
        await service.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
    clock[0] = boundary + timedelta(seconds=1)
    current = await service.summary(tenant_id=tenant_id)
    assert current.entitlement_status == "active" and current.version == 2
    assert current.provider_requests_used == 0 and current.provider_requests_remaining == 2
    replay = await service.settle_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert replay.replay and replay.entitlement_id == reserved.entitlement_id
