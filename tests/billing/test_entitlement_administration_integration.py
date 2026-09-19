from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing import EntitlementUsageService, UsageError
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity.models import Tenant

pytestmark = pytest.mark.integration


async def test_configuration_and_audit_commit_once_and_replay_keeps_usage(billing_database) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    service = EntitlementAdministrationService(session_factory=factory, clock=lambda: now)
    operator = PlatformEntitlementOperator("local-operator", "Local trial configuration")
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    first = await service.configure(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    assert not first.replayed and first.entitlement.version == 1
    assert first.entitlement.entitlement_id == configuration.entitlement_id
    usage = EntitlementUsageService(session_factory=factory, clock=lambda: now)
    operation_id = uuid4()
    await usage.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    await usage.settle_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    replay = await service.configure(
        tenant_id=tenant_id, operator=operator, configuration=configuration
    )
    assert replay.replayed
    assert replay.entitlement.provider_requests_used == 1
    shown = await service.show(
        tenant_id=tenant_id, entitlement_id=configuration.entitlement_id, operator=operator
    )
    assert shown == replay.entitlement
    assert await service.list(tenant_id=tenant_id, operator=operator) == (shown,)
    async with factory() as session:
        records = (
            await session.scalars(
                select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
            )
        ).all()
        audits = (
            await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        ).all()
    assert len(records) == len(audits) == 1
    assert audits[0].action == "billing.entitlement.configured"
    assert audits[0].actor_id is None
    assert audits[0].resource_id == configuration.entitlement_id
    assert audits[0].event_metadata["operator_id"] == operator.operator_id
    assert audits[0].event_metadata["reason"] == operator.reason


@pytest.mark.parametrize("identical", [True, False], ids=["same-receipt", "same-version"])
async def test_concurrent_configuration_serializes_first_version(
    billing_database, identical
) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    service = EntitlementAdministrationService(session_factory=factory, clock=lambda: now)
    operator = PlatformEntitlementOperator("local-operator", "Concurrent configuration")
    first = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    second = first if identical else first.model_copy(update={"entitlement_id": uuid4()})
    results = await asyncio.gather(
        *(
            service.configure(tenant_id=tenant_id, operator=operator, configuration=item)
            for item in (first, second)
        ),
        return_exceptions=True,
    )
    success = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, UsageError)]
    assert len(success) == (2 if identical else 1)
    assert sum(not result.replayed for result in success) == 1
    assert [error.code for error in errors] == (
        [] if identical else ["entitlement_version_conflict"]
    )
    assert len(await service.list(tenant_id=tenant_id, operator=operator)) == 1
    async with factory() as session:
        audits = (
            await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        ).all()
    assert len(audits) == 1


async def test_first_reservation_waits_for_configuration_commit_and_uses_its_entitlement(
    billing_database,
) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    ready, release = asyncio.Event(), asyncio.Event()

    class PausingCommit:
        @asynccontextmanager
        async def begin(self):
            async with factory.begin() as session:
                yield session
                ready.set()
                await asyncio.wait_for(release.wait(), timeout=5)

    service = EntitlementAdministrationService(session_factory=PausingCommit(), clock=lambda: now)
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    configuring = asyncio.create_task(
        service.configure(
            tenant_id=tenant_id,
            operator=PlatformEntitlementOperator("operator", "First trial"),
            configuration=configuration,
        )
    )
    reserving = None
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        usage = EntitlementUsageService(session_factory=factory, clock=lambda: now)
        reserving = asyncio.create_task(
            usage.reserve_provider_request(
                tenant_id=tenant_id,
                operation_id=uuid4(),
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(reserving), timeout=0.2)
        release.set()
        configured = await asyncio.wait_for(configuring, timeout=5)
        reserved = await asyncio.wait_for(reserving, timeout=5)
        assert reserved.ledgered and not reserved.replay
        assert reserved.entitlement_id == configured.entitlement.entitlement_id
        assert reserved.provider_requests_reserved == 1
    finally:
        release.set()
        tasks = [configuring] if reserving is None else [configuring, reserving]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_conflicts_do_not_rewrite_configurations_and_adjacent_period_is_allowed(
    billing_database,
) -> None:
    factory, (tenant_id, other_id) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    service = EntitlementAdministrationService(session_factory=factory, clock=lambda: now)
    operator = PlatformEntitlementOperator("local-operator", "Explicit trial")
    first = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    await service.configure(tenant_id=tenant_id, operator=operator, configuration=first)
    for changes, code in (
        ({"provider_request_limit": 10}, "entitlement_idempotency_conflict"),
        ({"entitlement_id": uuid4()}, "entitlement_version_conflict"),
        ({"entitlement_id": uuid4(), "expected_version": 1}, "entitlement_period_overlap"),
        (
            {
                "entitlement_id": uuid4(),
                "expected_version": 1,
                "period_start": now - timedelta(days=2),
                "period_end": now,
            },
            "entitlement_period_ended",
        ),
    ):
        changed = EntitlementConfiguration.model_validate({**first.model_dump(), **changes})
        with pytest.raises(UsageError, match=f"^{code}$"):
            await service.configure(tenant_id=tenant_id, operator=operator, configuration=changed)
    adjacent = EntitlementConfiguration.model_validate(
        {
            **first.model_dump(),
            "entitlement_id": uuid4(),
            "expected_version": 1,
            "period_start": first.period_end,
            "period_end": first.period_end + timedelta(days=1),
        }
    )
    await service.configure(tenant_id=tenant_id, operator=operator, configuration=adjacent)
    records = await service.list(tenant_id=tenant_id, operator=operator)
    assert [row.version for row in records] == [2, 1]
    assert all(row.provider_request_limit == 2 for row in records)
    with pytest.raises(UsageError, match=r"^entitlement_not_found$"):
        await service.show(
            tenant_id=other_id, entitlement_id=first.entitlement_id, operator=operator
        )
    assert await service.list(tenant_id=other_id, operator=operator) == ()
    with pytest.raises(UsageError, match=r"^entitlement_configuration_conflict$"):
        await service.configure(tenant_id=other_id, operator=operator, configuration=first)
    assert await service.list(tenant_id=other_id, operator=operator) == ()
    async with factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant.quota_bytes == 1024
        audits = (
            await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        ).all()
    assert len(audits) == 2


async def test_configuration_and_audit_roll_back_together(billing_database) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    operator = PlatformEntitlementOperator("local-operator", "Commit boundary failure")

    class FailingCommit:
        @asynccontextmanager
        async def begin(self):
            async with factory.begin() as session:
                yield session
                raise RuntimeError("injected commit failure")

    service = EntitlementAdministrationService(session_factory=FailingCommit(), clock=lambda: now)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        await service.configure(tenant_id=tenant_id, operator=operator, configuration=configuration)
    async with factory() as session:
        assert (
            await session.scalars(
                select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
            )
        ).all() == []
        assert (
            await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        ).all() == []


async def test_configuration_requires_operator_and_active_tenant(billing_database) -> None:
    factory, (tenant_id, _) = billing_database
    now = datetime(2026, 9, 14, tzinfo=UTC)
    service = EntitlementAdministrationService(session_factory=factory, clock=lambda: now)
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=now,
        period_end=now + timedelta(days=1),
        provider_request_limit=2,
    )
    owner = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    with pytest.raises(UsageError, match=r"^entitlement_operator_forbidden$"):
        await service.configure(tenant_id=tenant_id, operator=owner, configuration=configuration)
    async with factory.begin() as session:
        tenant = await session.get(Tenant, tenant_id)
        tenant.is_active = False
    with pytest.raises(UsageError, match=r"^usage_tenant_unavailable$"):
        await service.configure(
            tenant_id=tenant_id,
            operator=PlatformEntitlementOperator("local-operator", "Inactive tenant"),
            configuration=configuration,
        )
