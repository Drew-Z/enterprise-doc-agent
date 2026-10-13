import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
    ProductQuotaConfiguration,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_models import ProductQuota, ProductUsageReservation
from enterprise_doc_core.billing.product_usage import ProductUsageService
from enterprise_doc_core.config import AppEnvironment

pytestmark = pytest.mark.integration


async def test_product_migration_round_trip_and_history_guard(billing_database):
    sessions, (tenant_id, _) = billing_database
    migration = import_module(
        "enterprise_doc_core.db.migrations.versions.20260924_0029_product_usage"
    )

    def migrate(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, direction)()

    async with sessions.begin() as session:
        connection = await session.connection()
        await connection.run_sync(migrate, "downgrade")
        await connection.run_sync(migrate, "upgrade")
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("migration-test", "retain history"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now,
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
        ),
    )
    with pytest.raises(RuntimeError, match="product_usage_history_present"):
        async with sessions.begin() as session:
            connection = await session.connection()
            await connection.run_sync(migrate, "downgrade")


async def test_product_reservations_rollback_and_foreign_tenant_cannot_reference_quota(
    billing_database,
):
    sessions, (tenant_id, other_tenant) = billing_database
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("quota-test", "atomicity"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now,
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
            agent_task_limit=1,
        ),
    )
    usage = ProductUsageService(session_factory=sessions, app_env=AppEnvironment.PRODUCTION)
    with pytest.raises(RuntimeError, match="abort transaction"):
        async with sessions.begin() as session:
            await usage.reserve(
                tenant_id=tenant_id,
                operation_id=uuid4(),
                metric=ProductMetric.AGENT_TASK,
                session=session,
            )
            raise RuntimeError("abort transaction")
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert quota.units_reserved == quota.units_used == 0
        quota_id = quota.id
    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            session.add(
                ProductUsageReservation(
                    tenant_id=other_tenant,
                    quota_id=quota_id,
                    operation_id=uuid4(),
                    metric="agent_task",
                    quantity=1,
                    state="reserved",
                    reserved_at=now,
                )
            )
            await session.flush()
    with pytest.raises(UsageError, match="usage_entitlement_inactive"):
        await usage.reserve(
            tenant_id=other_tenant, operation_id=uuid4(), metric=ProductMetric.AGENT_TASK
        )


async def test_legacy_period_can_receive_products_once_without_resetting_usage(billing_database):
    sessions, (tenant_id, other_tenant) = billing_database
    now = datetime.now(UTC)
    period_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                id=period_id,
                tenant_id=tenant_id,
                version=1,
                plan_code="invited",
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(days=1),
                provider_request_limit=10,
                provider_requests_used=3,
                provider_requests_reserved=0,
                created_at=now,
                updated_at=now,
            )
        )
    admin = EntitlementAdministrationService(session_factory=sessions)
    operator = PlatformEntitlementOperator("quota-test", "enable current enterprise")
    configuration = ProductQuotaConfiguration(
        entitlement_id=period_id,
        expected_version=1,
        agent_task_limit=2,
        document_bytes_limit=4096,
    )
    with pytest.raises(UsageError, match="entitlement_not_found"):
        await admin.configure_products(
            tenant_id=other_tenant, operator=operator, configuration=configuration
        )
    first = await admin.configure_products(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    assert not first.replayed
    usage = ProductUsageService(session_factory=sessions, app_env=AppEnvironment.PRODUCTION)
    await usage.reserve(tenant_id=tenant_id, operation_id=uuid4(), metric=ProductMetric.AGENT_TASK)
    replay = await admin.configure_products(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    assert replay.replayed
    assert replay.entitlement.product_quotas[0].reserved == 1
    assert replay.entitlement.provider_requests_used == 3
    with pytest.raises(UsageError, match="entitlement_idempotency_conflict"):
        await admin.configure_products(
            tenant_id=tenant_id,
            operator=operator,
            configuration=configuration.model_copy(update={"agent_task_limit": 3}),
        )
    async with sessions() as session:
        row = await session.scalar(
            select(TenantEntitlement).where(TenantEntitlement.id == period_id)
        )
        assert row.version == 1 and row.provider_requests_used == 3


async def test_operator_configures_and_replays_independent_product_quotas(billing_database):
    sessions, (tenant_id, _) = billing_database
    now = datetime.now(UTC)
    service = EntitlementAdministrationService(session_factory=sessions, clock=lambda: now)
    operator = PlatformEntitlementOperator("quota-test", "invited enterprise allocation")
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="invited",
        period_start=now - timedelta(minutes=1),
        period_end=now + timedelta(days=30),
        provider_request_limit=7,
        agent_task_limit=2,
        document_bytes_limit=4096,
    )
    first = await service.configure(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    replay = await service.configure(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    shown = await service.show(
        tenant_id=tenant_id, entitlement_id=configuration.entitlement_id, operator=operator
    )
    assert not first.replayed and replay.replayed
    assert first.entitlement == replay.entitlement == shown
    assert shown.provider_request_limit == 7
    assert {
        quota.metric: (quota.limit, quota.used, quota.reserved) for quota in shown.product_quotas
    } == {
        "agent_task": (2, 0, 0),
        "document_bytes": (4096, 0, 0),
    }


async def test_concurrent_last_task_reservation_is_separate_from_document_quota(billing_database):
    sessions, (tenant_id, _) = billing_database
    now = datetime.now(UTC)
    admin = EntitlementAdministrationService(session_factory=sessions, clock=lambda: now)
    operator = PlatformEntitlementOperator("quota-test", "allocation")
    period_id = uuid4()
    await admin.configure(
        tenant_id=tenant_id,
        operator=operator,
        configuration=EntitlementConfiguration(
            entitlement_id=period_id,
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(seconds=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=5,
            agent_task_limit=1,
            document_bytes_limit=1024,
        ),
    )
    usage = ProductUsageService(session_factory=sessions, app_env=AppEnvironment.PRODUCTION)
    results = await asyncio.gather(
        *(
            usage.reserve(
                tenant_id=tenant_id, operation_id=uuid4(), metric=ProductMetric.AGENT_TASK
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    failures = [item for item in results if isinstance(item, UsageError)]
    assert len(failures) == 1 and failures[0].code == "usage_limit_reached"
    winner = next(item for item in results if not isinstance(item, Exception))
    replay = await usage.reserve(
        tenant_id=tenant_id, operation_id=winner.operation_id, metric=ProductMetric.AGENT_TASK
    )
    assert replay.replay and replay.reservation_id == winner.reservation_id
    assert winner.status == "reserved" and winner.ledgered
    await usage.reserve(
        tenant_id=tenant_id,
        operation_id=uuid4(),
        metric=ProductMetric.DOCUMENT_BYTES,
        quantity=1024,
    )
    snapshot = await admin.show(tenant_id=tenant_id, entitlement_id=period_id, operator=operator)
    assert {quota.metric: quota.reserved for quota in snapshot.product_quotas} == {
        "agent_task": 1,
        "document_bytes": 1024,
    }
    assert snapshot.provider_requests_reserved == 0


async def test_waiting_task_settles_original_period_once_and_cancel_releases(billing_database):
    sessions, (tenant_id, _) = billing_database
    now = datetime.now(UTC)
    admin = EntitlementAdministrationService(session_factory=sessions, clock=lambda: now)
    operator = PlatformEntitlementOperator("quota-test", "allocation")
    first_period, next_period = uuid4(), uuid4()
    for version, period_id in enumerate((first_period, next_period)):
        await admin.configure(
            tenant_id=tenant_id,
            operator=operator,
            configuration=EntitlementConfiguration(
                entitlement_id=period_id,
                expected_version=version,
                plan_code="invited",
                period_start=now + timedelta(days=version) - timedelta(seconds=1),
                period_end=now + timedelta(days=version + 1) - timedelta(seconds=1),
                provider_request_limit=5,
                agent_task_limit=2,
                document_bytes_limit=1024,
            ),
        )
    clock = [now]
    usage = ProductUsageService(
        session_factory=sessions, app_env=AppEnvironment.PRODUCTION, clock=lambda: clock[0]
    )
    done, cancelled = uuid4(), uuid4()
    for operation_id in (done, cancelled):
        await usage.reserve(
            tenant_id=tenant_id, operation_id=operation_id, metric=ProductMetric.AGENT_TASK
        )
    # Approval outlives both the old short reservation TTL and the original period.
    clock[0] += timedelta(days=1)
    results = await asyncio.gather(
        *(
            usage.settle(
                tenant_id=tenant_id,
                operation_id=done,
                metric=ProductMetric.AGENT_TASK,
                source="agent.succeeded",
            )
            for _ in range(2)
        )
    )
    assert sorted(result.replay for result in results) == [False, True]
    assert all(
        result.entitlement_id == first_period and result.status == "consumed" for result in results
    )
    for _ in range(2):
        assert (
            await usage.release(
                tenant_id=tenant_id,
                operation_id=cancelled,
                metric=ProductMetric.AGENT_TASK,
                source="agent.cancelled",
            )
        ).status == "released"
    with pytest.raises(UsageError, match="usage_reservation_not_settleable"):
        await usage.settle(
            tenant_id=tenant_id, operation_id=cancelled, metric=ProductMetric.AGENT_TASK
        )
    before = await admin.show(tenant_id=tenant_id, entitlement_id=first_period, operator=operator)
    after = await admin.show(tenant_id=tenant_id, entitlement_id=next_period, operator=operator)
    assert [(q.used, q.reserved) for q in before.product_quotas] == [(1, 0), (0, 0)]
    assert [(q.used, q.reserved) for q in after.product_quotas] == [(0, 0), (0, 0)]
