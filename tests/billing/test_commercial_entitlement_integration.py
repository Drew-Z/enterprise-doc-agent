from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from enterprise_doc_core.billing import EntitlementUsageService, UsageError
from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.config import AppEnvironment

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("environment", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION])
async def test_formal_tenant_needs_a_period_before_new_generation(
    billing_database, environment
) -> None:
    sessions, (tenant_id, _) = billing_database
    service = EntitlementUsageService(session_factory=sessions, app_env=environment)
    with pytest.raises(UsageError, match=r"^usage_entitlement_inactive$"):
        await service.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
    summary = await service.summary(tenant_id=tenant_id)
    assert summary.entitlement_status == "inactive"
    assert summary.provider_requests_remaining == 0
    assert not summary.enabled
    assert summary.resources.storage_limit_bytes == 1024
    async with sessions() as session:
        for model in (UsageReservation, UsageEvent):
            assert (
                await session.scalar(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
                )
                == 0
            )


@pytest.mark.parametrize("limit", [None, 0, 2])
async def test_formal_generation_requires_a_finite_positive_remaining_limit(
    billing_database, limit
) -> None:
    sessions, (tenant_id, _) = billing_database
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                tenant_id=tenant_id,
                plan_code="trial",
                version=1,
                period_start=now - timedelta(hours=1),
                period_end=now + timedelta(hours=1),
                provider_request_limit=limit,
                created_at=now,
                updated_at=now,
            )
        )
    service = EntitlementUsageService(
        session_factory=sessions, app_env=AppEnvironment.PRODUCTION, clock=lambda: now
    )
    operation_id = uuid4()
    if limit in (None, 0):
        code = "usage_entitlement_inactive" if limit is None else "usage_limit_reached"
        with pytest.raises(UsageError, match=f"^{code}$"):
            await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
        summary = await service.summary(tenant_id=tenant_id)
        assert summary.provider_requests_remaining == 0
        if limit is None:
            assert summary.entitlement_status == "inactive" and not summary.enabled
    else:
        first = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=operation_id
        )
        second = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=operation_id
        )
        assert first.ledgered and second.replay
        assert first.reservation_id == second.reservation_id
        assert (await service.summary(tenant_id=tenant_id)).provider_requests_remaining == 1


async def test_formal_settlement_requires_a_durable_reservation(billing_database) -> None:
    sessions, (tenant_id, _) = billing_database
    service = EntitlementUsageService(session_factory=sessions, app_env=AppEnvironment.PRODUCTION)
    with pytest.raises(UsageError, match=r"^usage_reservation_not_found$"):
        await service.settle_provider_request(tenant_id=tenant_id, operation_id=uuid4())
    # Releasing work which failed before reservation remains a safe no-op.
    result = await service.release_provider_request(tenant_id=tenant_id, operation_id=uuid4())
    assert not result.ledgered


async def test_strict_policy_keeps_old_reservations_settleable_after_period_end(
    billing_database,
) -> None:
    sessions, (tenant_id, _) = billing_database
    boundary = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                tenant_id=tenant_id,
                plan_code="historical",
                version=1,
                period_start=boundary - timedelta(hours=1),
                period_end=boundary,
                provider_request_limit=None,
                created_at=boundary,
                updated_at=boundary,
            )
        )
    local = EntitlementUsageService(
        session_factory=sessions, clock=lambda: boundary - timedelta(seconds=1)
    )
    consume_id, release_id = uuid4(), uuid4()
    for operation_id in (consume_id, release_id):
        await local.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    formal = EntitlementUsageService(
        session_factory=sessions, app_env=AppEnvironment.PRODUCTION, clock=lambda: boundary
    )
    result = await formal.settle_provider_request(tenant_id=tenant_id, operation_id=consume_id)
    assert result.ledgered and result.status == "consumed"
    assert (
        await formal.settle_provider_request(tenant_id=tenant_id, operation_id=consume_id)
    ).replay
    assert (
        await formal.release_provider_request(tenant_id=tenant_id, operation_id=release_id)
    ).status == "released"
    with pytest.raises(UsageError, match=r"^usage_entitlement_inactive$"):
        await formal.reserve_provider_request(tenant_id=tenant_id, operation_id=uuid4())
