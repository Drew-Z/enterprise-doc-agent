from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_doc_core.billing import EntitlementUsageService, UsageError
from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.db import create_session_factory

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "FOUNDATION_TEST_DATABASE_URL",
    "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
)


def _tenant() -> UUID:
    value = uuid4()
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (id, name, slug, quota_bytes) VALUES (%s, %s, %s, %s)",
            (value, "billing integration", f"billing-{value.hex}", 1024),
        )
    return value


def _remove(*tenant_ids: UUID) -> None:
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        for tenant_id in tenant_ids:
            cursor.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def _engine():
    return create_async_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://"))


async def _add_entitlement(factory, tenant_id: UUID, *, limit: int, start: datetime, end: datetime):
    async with factory.begin() as session:
        session.add(
            TenantEntitlement(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_code="trial",
                version=1,
                period_start=start,
                period_end=end,
                provider_request_limit=limit,
                provider_requests_used=0,
                provider_requests_reserved=0,
                created_at=start,
                updated_at=start,
            )
        )


async def test_last_provider_request_is_reserved_once_under_concurrency() -> None:
    tenant_id = _tenant()
    engine = _engine()
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        await _add_entitlement(
            factory,
            tenant_id,
            limit=1,
            start=now - timedelta(minutes=1),
            end=now + timedelta(hours=1),
        )
        operation_id = uuid4()
        services = [EntitlementUsageService(session_factory=factory) for _ in range(2)]
        results = await asyncio.gather(
            *(
                service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
                for service in services
            ),
            return_exceptions=True,
        )
        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, UsageError)]
        assert len(successes) == 2
        assert sum(not item.replay for item in successes) == 1
        assert failures == []
        async with factory() as session:
            row = await session.scalar(
                select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
            )
            assert row is not None
            assert row.provider_requests_reserved == 1
    finally:
        await engine.dispose()
        _remove(tenant_id)


async def test_expired_settlement_releases_reservation_and_unknown_cost_stays_null() -> None:
    tenant_id = _tenant()
    engine = _engine()
    factory = create_session_factory(engine)
    start = datetime.now(UTC).replace(microsecond=0)
    try:
        await _add_entitlement(
            factory,
            tenant_id,
            limit=2,
            start=start - timedelta(minutes=1),
            end=start + timedelta(hours=1),
        )
        operation_id = uuid4()
        service = EntitlementUsageService(
            session_factory=factory, clock=lambda: start, reservation_ttl_seconds=1
        )
        await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
        expired = EntitlementUsageService(
            session_factory=factory, clock=lambda: start + timedelta(seconds=2)
        )
        with pytest.raises(UsageError, match="usage_reservation_expired"):
            await expired.settle_provider_request(
                tenant_id=tenant_id, operation_id=operation_id, usage={"total_tokens": -1}
            )
        summary = await expired.summary(tenant_id=tenant_id, now=start + timedelta(seconds=2))
        assert summary.provider_requests_reserved == 0
        assert summary.provider_requests_used == 0
        assert summary.cost_status == "unknown"
        async with factory() as session:
            reservation = await session.scalar(
                select(UsageReservation).where(UsageReservation.operation_id == operation_id)
            )
            assert reservation is not None and reservation.state == "released"
    finally:
        await engine.dispose()
        _remove(tenant_id)


async def test_same_operation_is_independent_across_tenants() -> None:
    first, second = _tenant(), _tenant()
    engine = _engine()
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        for tenant_id in (first, second):
            await _add_entitlement(
                factory,
                tenant_id,
                limit=1,
                start=now - timedelta(minutes=1),
                end=now + timedelta(hours=1),
            )
        operation_id = uuid4()
        service = EntitlementUsageService(session_factory=factory)
        results = await asyncio.gather(
            service.reserve_provider_request(tenant_id=first, operation_id=operation_id),
            service.reserve_provider_request(tenant_id=second, operation_id=operation_id),
        )
        assert all(item.ledgered and not item.replay for item in results)
        assert {item.tenant_id for item in results} == {first, second}
    finally:
        await engine.dispose()
        _remove(first, second)


async def test_period_selection_uses_half_open_boundaries_and_conflicts_are_stable() -> None:
    tenant_id = _tenant()
    engine = _engine()
    factory = create_session_factory(engine)
    boundary = datetime(2026, 9, 20, tzinfo=UTC)
    try:
        async with factory.begin() as session:
            session.add_all(
                [
                    TenantEntitlement(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        plan_code="old",
                        version=1,
                        period_start=boundary - timedelta(days=1),
                        period_end=boundary,
                        provider_request_limit=0,
                        created_at=boundary,
                        updated_at=boundary,
                    ),
                    TenantEntitlement(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        plan_code="new",
                        version=2,
                        period_start=boundary,
                        period_end=boundary + timedelta(days=1),
                        provider_request_limit=2,
                        created_at=boundary,
                        updated_at=boundary,
                    ),
                ]
            )
        service = EntitlementUsageService(session_factory=factory, clock=lambda: boundary)
        operation_id = uuid4()
        result = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=operation_id, quantity=1
        )
        assert result.ledgered and result.provider_request_limit == 2
        with pytest.raises(UsageError, match="usage_idempotency_conflict"):
            await service.reserve_provider_request(
                tenant_id=tenant_id, operation_id=operation_id, quantity=2
            )
    finally:
        await engine.dispose()
        _remove(tenant_id)
