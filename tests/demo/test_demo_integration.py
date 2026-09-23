import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, func, select
from tests.browser_sessions.conftest import BrowserDatabase

from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.browser_sessions.errors import BrowserContextStale, BrowserSessionInvalid
from enterprise_doc_core.demo.limits import finish_attempt, reserve_attempt
from enterprise_doc_core.demo.models import DemoDay, DemoWorkspace
from enterprise_doc_core.demo.service import DemoService
from enterprise_doc_core.demo.settings import DemoError, DemoSettings
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

pytestmark = pytest.mark.integration


async def test_isolated_visitors_no_external_identity_and_expiry(demo_db: BrowserDatabase) -> None:
    now = datetime.now(UTC)
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True), clock=lambda: now
    )
    first, second = await asyncio.gather(service.start(), service.start())
    assert first.snapshot.tenant_id != second.snapshot.tenant_id
    assert first.snapshot.actor_id != second.snapshot.actor_id
    assert first.credential != second.credential
    assert (await service.get(first.credential)) == first.snapshot
    with pytest.raises(BrowserContextStale):
        await service.get(second.credential, first.snapshot.context_version)
    async with demo_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ExternalIdentityBinding)) == 0
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 2
        assert await session.scalar(select(func.count()).select_from(User)) == 2
        for visitor in (first, second):
            tenant = await session.get(Tenant, visitor.snapshot.tenant_id)
            assert tenant is not None and tenant.name == "演示企业"
            membership = await session.scalar(
                select(Membership).where(Membership.tenant_id == tenant.id)
            )
            assert membership is not None and membership.user_id == visitor.snapshot.actor_id
            assert membership.role == "owner"
            entitlement = await session.scalar(
                select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant.id)
            )
            assert entitlement is not None and entitlement.plan_code == "public-demo"
    now += timedelta(hours=3)
    with pytest.raises(BrowserSessionInvalid):
        await service.get(first.credential)


async def test_capacity_is_atomic_and_failed_start_does_not_provision(
    demo_db: BrowserDatabase,
) -> None:
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True, max_workspaces=1)
    )
    results = await asyncio.gather(service.start(), service.start(), return_exceptions=True)
    assert sum(isinstance(r, DemoError) for r in results) == 1
    async with demo_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert await session.scalar(select(DemoDay.workspaces_created)) == 1


async def test_logout_is_idempotent_and_cannot_logout_other_visitor(
    demo_db: BrowserDatabase,
) -> None:
    service = DemoService(session_factory=demo_db.sessions, settings=DemoSettings(enabled=True))
    first, second = await service.start(), await service.start()
    with pytest.raises(BrowserContextStale):
        await service.logout(first.credential, second.snapshot.context_version)
    assert await service.logout(first.credential, first.snapshot.context_version)
    assert await service.logout(first.credential, first.snapshot.context_version)
    with pytest.raises(BrowserSessionInvalid):
        await service.get(first.credential)
    assert await service.get(second.credential) == second.snapshot


async def test_failed_generations_keep_hard_budget_and_global_concurrency(
    demo_db: BrowserDatabase,
) -> None:
    service = DemoService(session_factory=demo_db.sessions, settings=DemoSettings(enabled=True))
    first, second = await service.start(), await service.start()
    now = datetime.now(UTC)
    attempt = uuid4()
    async with demo_db.sessions.begin() as session:
        await reserve_attempt(
            session, first.snapshot.tenant_id, attempt, now, now + timedelta(minutes=2)
        )
    async with demo_db.sessions.begin() as session:
        with pytest.raises(DemoError, match="demo_generation_busy"):
            await reserve_attempt(
                session, second.snapshot.tenant_id, uuid4(), now, now + timedelta(minutes=2)
            )
    async with demo_db.sessions.begin() as session:
        await finish_attempt(session, first.snapshot.tenant_id, attempt)
    for _ in range(5):
        attempt = uuid4()
        async with demo_db.sessions.begin() as session:
            await reserve_attempt(
                session, first.snapshot.tenant_id, attempt, now, now + timedelta(minutes=2)
            )
            await finish_attempt(session, first.snapshot.tenant_id, attempt)
    async with demo_db.sessions.begin() as session:
        with pytest.raises(DemoError, match="demo_attempt_limit"):
            await reserve_attempt(
                session, first.snapshot.tenant_id, uuid4(), now, now + timedelta(minutes=2)
            )
    async with demo_db.sessions() as session:
        assert await session.scalar(select(DemoDay.attempts_used)) == 6
        assert (
            await session.scalar(
                select(DemoWorkspace.attempts_used).where(
                    DemoWorkspace.tenant_id == first.snapshot.tenant_id
                )
            )
            == 6
        )


async def test_global_daily_budget_does_not_reset_with_new_visitor(
    demo_db: BrowserDatabase,
) -> None:
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True, daily_attempt_limit=1)
    )
    first, second = await service.start(), await service.start()
    now, attempt = datetime.now(UTC), uuid4()
    async with demo_db.sessions.begin() as session:
        await reserve_attempt(
            session, first.snapshot.tenant_id, attempt, now, now + timedelta(minutes=2)
        )
        await finish_attempt(session, first.snapshot.tenant_id, attempt)
    async with demo_db.sessions.begin() as session:
        with pytest.raises(DemoError, match="demo_daily_limit"):
            await reserve_attempt(
                session, second.snapshot.tenant_id, uuid4(), now, now + timedelta(minutes=2)
            )


def migrate(connection: Connection, direction: str) -> None:
    module = import_module("enterprise_doc_core.db.migrations.versions.20260923_0027_public_demo")
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def test_migration_round_trip(demo_db: BrowserDatabase) -> None:
    async with demo_db.engine.begin() as connection:
        await connection.run_sync(migrate, "downgrade")
        await connection.run_sync(migrate, "upgrade")
    service = DemoService(session_factory=demo_db.sessions, settings=DemoSettings(enabled=True))
    issued = await service.start()
    assert await service.get(issued.credential) == issued.snapshot
