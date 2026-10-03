import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_models import ProductQuota
from enterprise_doc_core.billing.product_usage import ProductUsageService
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.billing.reconciliation import UsageReconciliationService
from enterprise_doc_core.billing.reconciliation_contracts import UsageExportWindow
from enterprise_doc_core.billing.service import EntitlementUsageService
from enterprise_doc_core.config import AppEnvironment
from enterprise_doc_core.identity.models import Tenant

pytestmark = pytest.mark.integration
OPERATOR = PlatformEntitlementOperator("reconciliation-test", "Synthetic supplier reconciliation")


def dispatch(tenant_id, when, state="responded", **values):
    return ProviderDispatch(
        tenant_id=tenant_id,
        operation_id=uuid4(),
        kind="agent",
        provider="test",
        model="test-model",
        channel_hash="a" * 64,
        state=state,
        started_at=when,
        **values,
    )


async def test_export_separates_cross_period_business_events_and_unknown_calls(billing_database):
    sessions, (tenant_id, other_id) = billing_database
    start = datetime.now(UTC).replace(microsecond=0)
    period = uuid4()
    old = start - timedelta(days=1)
    await EntitlementAdministrationService(session_factory=sessions, clock=lambda: old).configure(
        tenant_id=tenant_id,
        operator=OPERATOR,
        configuration=EntitlementConfiguration(
            entitlement_id=period,
            expected_version=0,
            plan_code="invited",
            period_start=old,
            period_end=start,
            provider_request_limit=2,
            agent_task_limit=2,
            document_bytes_limit=100,
        ),
    )
    clock = [old + timedelta(minutes=1)]
    products = ProductUsageService(
        session_factory=sessions, app_env=AppEnvironment.PRODUCTION, clock=lambda: clock[0]
    )
    tasks = [uuid4(), uuid4()]
    for operation in tasks:
        await products.reserve(
            tenant_id=tenant_id, operation_id=operation, metric=ProductMetric.AGENT_TASK
        )
    document = uuid4()
    await products.reserve(
        tenant_id=tenant_id, operation_id=document, metric=ProductMetric.DOCUMENT_BYTES, quantity=23
    )
    presales = EntitlementUsageService(
        session_factory=sessions, clock=lambda: clock[0], reservation_ttl_seconds=172800
    )
    presales_op = uuid4()
    await presales.reserve_provider_request(tenant_id=tenant_id, operation_id=presales_op)
    clock[0] = start
    await products.settle(
        tenant_id=tenant_id, operation_id=tasks[0], metric=ProductMetric.AGENT_TASK
    )
    await products.release(
        tenant_id=tenant_id, operation_id=tasks[1], metric=ProductMetric.AGENT_TASK
    )
    await products.settle(
        tenant_id=tenant_id, operation_id=document, metric=ProductMetric.DOCUMENT_BYTES
    )
    await presales.settle_provider_request(tenant_id=tenant_id, operation_id=presales_op)
    states = [
        "dispatched",
        "responded",
        "http_error",
        "timeout",
        "transport_error",
        "cancelled",
        "unknown",
    ]
    async with sessions.begin() as session:
        session.add_all(
            dispatch(
                tenant_id,
                start,
                state,
                provider_response_id="same-id",
                total_tokens=7 if state == "responded" else None,
            )
            for state in states
        )
        session.add_all(
            [
                dispatch(tenant_id, start - timedelta(microseconds=1)),
                dispatch(tenant_id, start + timedelta(days=1)),
                dispatch(other_id, start, provider_response_id="other-secret"),
            ]
        )
    window = UsageExportWindow(start=start, end=start + timedelta(days=1))
    service = UsageReconciliationService(session_factory=sessions)
    result = await service.export(tenant_id=tenant_id, operator=OPERATOR, window=window)
    assert {c.state for c in result.provider_calls} == set(states)
    assert len(result.provider_calls) == 7  # Never deduplicate by supplier ID.
    assert all(c.estimated_cost is None and c.currency is None for c in result.provider_calls)
    assert len(result.business_events) == 4
    assert {e.entitlement_id for e in result.business_events} == {period}
    assert {(e.metric, e.event_type, e.quantity) for e in result.business_events} == {
        ("agent_task", "consume", 1),
        ("agent_task", "release", 1),
        ("document_bytes", "consume", 23),
        ("provider_request", "consume", 1),
    }
    assert result.summary.unknown_cost_records == 7
    assert result.summary.unresolved_records == 5
    assert result.summary.known_total_tokens == 7
    assert result.summary.missing_token_records == 6
    assert "other-secret" not in json.dumps(asdict(result), default=str)
    replay = await service.export(tenant_id=tenant_id, operator=OPERATOR, window=window)
    assert (
        replay.provider_calls == result.provider_calls
        and replay.business_events == result.business_events
    )
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "agent_task"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (1, 0)
        entitlement = await session.get(TenantEntitlement, period)
        assert entitlement.provider_requests_used == 1
    with pytest.raises(UsageError, match="reconciliation_export_limit_exceeded"):
        await service.export(
            tenant_id=tenant_id,
            operator=OPERATOR,
            window=window.model_copy(update={"limit_per_source": 2}),
        )
    with pytest.raises(UsageError, match="reconciliation_tenant_not_found"):
        await service.export(tenant_id=uuid4(), operator=OPERATOR, window=window)


async def test_export_database_enforces_read_only_and_one_snapshot(billing_database):
    sessions, (tenant_id, _) = billing_database
    start = datetime.now(UTC)
    observed = []

    class ObservedSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            result = await super().scalar(statement, *args, **kwargs)
            if "FROM tenants" in str(statement) and not observed:
                assert await super().scalar(text("SHOW transaction_read_only")) == "on"
                assert await super().scalar(text("SHOW transaction_isolation")) == "repeatable read"
                with pytest.raises(DBAPIError) as rejected:
                    async with self.begin_nested():
                        await self.execute(
                            update(Tenant)
                            .where(Tenant.id == tenant_id)
                            .values(name="must not write")
                        )
                assert rejected.value.orig.sqlstate == "25006"
                async with sessions.begin() as concurrent:
                    concurrent.add(dispatch(tenant_id, start))
                observed.append(True)
            return result

    window = UsageExportWindow(start=start, end=start + timedelta(hours=1))
    service = UsageReconciliationService(
        session_factory=async_sessionmaker(
            sessions.kw["bind"], class_=ObservedSession, expire_on_commit=False
        )
    )
    result = await service.export(tenant_id=tenant_id, operator=OPERATOR, window=window)
    assert observed == [True] and result.provider_calls == ()
    refreshed = await UsageReconciliationService(session_factory=sessions).export(
        tenant_id=tenant_id, operator=OPERATOR, window=window
    )
    assert len(refreshed.provider_calls) == 1
    async with sessions() as session:
        assert (await session.get(Tenant, tenant_id)).name != "must not write"


async def test_identifier_migration_preserves_legacy_rows_and_refuses_losing_ids(billing_database):
    sessions, (tenant_id, _) = billing_database
    module = import_module(
        "enterprise_doc_core.db.migrations.versions.20260924_0031_provider_reconciliation"
    )

    def migrate(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()

    async with sessions.begin() as session:
        row = dispatch(tenant_id, datetime.now(UTC), "timeout")
        session.add(row)
        await session.flush()
        row_id = row.id
        connection = await session.connection()
        await connection.run_sync(migrate, "downgrade")
        await connection.run_sync(migrate, "upgrade")
    async with sessions.begin() as session:
        restored = await session.get(ProviderDispatch, row_id)
        assert restored.state == "timeout" and restored.provider_request_id is None
        restored.provider_request_id = "req-keep"
    with pytest.raises(RuntimeError, match="provider_reconciliation_history_present"):
        async with sessions.begin() as session:
            await (await session.connection()).run_sync(migrate, "downgrade")
    async with sessions() as session:
        assert (await session.get(ProviderDispatch, row_id)).provider_request_id == "req-keep"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderDispatch)
                .where(ProviderDispatch.tenant_id == tenant_id)
            )
            == 1
        )


async def test_private_operator_exports_real_database_and_limit_failure_has_no_rows(
    billing_database,
):
    from enterprise_doc_core.operations.cli import OperationsSettings, build_parser, run_command

    sessions, (tenant_id, _) = billing_database
    start = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add_all([dispatch(tenant_id, start), dispatch(tenant_id, start, "cancelled")])
    url = sessions.kw["bind"].url
    settings = OperationsSettings(
        app_env="staging", database={"url": url.render_as_string(hide_password=False)}
    )
    arguments = [
        "--environment",
        "staging",
        "--database-host",
        url.host,
        "--database-port",
        str(url.port or 5432),
        "--database-name",
        url.database,
        "--operator",
        OPERATOR.operator_id,
        "--reason",
        OPERATOR.reason,
        "usage",
        "export",
        "--tenant-id",
        str(tenant_id),
        "--start",
        start.isoformat(),
        "--end",
        (start + timedelta(hours=1)).isoformat(),
    ]
    code, output = await run_command(build_parser().parse_args(arguments), settings)
    assert code == 0 and output["status"] == "confirmed"
    assert output["export"]["summary"]["unknown_cost_records"] == 2
    code, output = await run_command(
        build_parser().parse_args([*arguments, "--limit-per-source", "1"]), settings
    )
    assert code == 1 and output["code"] == "reconciliation_export_limit_exceeded"
    assert "export" not in output
