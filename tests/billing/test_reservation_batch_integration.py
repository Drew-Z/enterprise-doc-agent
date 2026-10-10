import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import event, select, text

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.billing.service import EntitlementUsageService

pytestmark = pytest.mark.integration


async def configured(sessions, tenant_id, *, limit=4, ttl=60):
    now = datetime.now(UTC)
    entitlement_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                id=entitlement_id,
                tenant_id=tenant_id,
                plan_code="batch-test",
                version=1,
                period_start=now - timedelta(days=1),
                period_end=now + timedelta(days=1),
                provider_request_limit=limit,
            )
        )
    return (
        EntitlementUsageService(
            session_factory=sessions, clock=lambda: now, reservation_ttl_seconds=ttl
        ),
        entitlement_id,
        now,
    )


async def test_new_reservation_commits_in_six_sql_with_exact_receipt(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, now = await configured(sessions, tenant_id)
    operation_id = uuid4()
    engine = sessions.kw["bind"].sync_engine
    statements = []

    def count(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        result = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=operation_id, quantity=2
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert result.ledgered and not result.replay and result.status == "reserved"
    assert result.provider_requests_reserved == 2 and result.provider_requests_used == 0
    assert result.provider_request_limit == 4
    async with sessions() as session:
        reservation = (await session.scalars(select(UsageReservation))).one()
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        assert reservation.id == result.reservation_id
        assert reservation.entitlement_id == entitlement_id
        assert reservation.operation_id == operation_id and reservation.tenant_id == tenant_id
        assert reservation.metric == "provider_request" and reservation.quantity == 2
        assert reservation.state == "reserved"
        assert reservation.reserved_at == reservation.created_at == reservation.updated_at == now
        assert reservation.expires_at == result.expires_at == now + timedelta(seconds=60)
        assert reservation.consumed_at is None and reservation.released_at is None
        assert entitlement.provider_requests_reserved == 2
    assert len(statements) <= 6, statements


async def test_reservation_and_settlement_update_counters_and_write_event(billing_database):
    """Former ORM-queue unit fake, now verifying real persisted writes and usage."""
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, _ = await configured(sessions, tenant_id, limit=2)
    operation_id = uuid4()
    result = await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert result.ledgered and result.provider_requests_reserved == 1
    async with sessions() as session:
        reservation = await session.get(UsageReservation, result.reservation_id)
        assert reservation.state == "reserved"
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        assert entitlement.provider_requests_reserved == 1
    settled = await service.settle_provider_request(
        tenant_id=tenant_id,
        operation_id=operation_id,
        usage={"total_tokens": 7},
        provider="provider",
    )
    assert settled.status == "consumed"
    async with sessions() as session:
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        reservation = await session.get(UsageReservation, result.reservation_id)
        consumption = (await session.scalars(select(UsageEvent))).one()
        assert entitlement.provider_requests_used == 1
        assert entitlement.provider_requests_reserved == 0
        assert reservation.state == "consumed"
        assert consumption.event_type == "consume" and consumption.total_tokens == 7
        assert consumption.provider == "provider" and consumption.quantity == 1
        assert consumption.operation_id == operation_id
        assert consumption.reservation_id == result.reservation_id
        assert consumption.estimated_cost is None and consumption.currency is None


async def test_same_transaction_reuses_current_counters_and_reservations(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, _ = await configured(sessions, tenant_id, limit=3)
    first_id, second_id = uuid4(), uuid4()
    async with sessions.begin() as session:
        first = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=first_id, quantity=2, session=session
        )
        second = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=second_id, session=session
        )
        assert first.provider_requests_reserved == 2 and second.provider_requests_reserved == 3
        with pytest.raises(UsageError, match="usage_limit_reached"):
            await service.reserve_provider_request(
                tenant_id=tenant_id, operation_id=uuid4(), session=session
            )
        consumed = await service.settle_provider_request(
            tenant_id=tenant_id, operation_id=first_id, session=session
        )
        assert consumed.provider_requests_used == 2 and consumed.provider_requests_reserved == 1
        released = await service.release_provider_request(
            tenant_id=tenant_id, operation_id=second_id, session=session
        )
        assert released.provider_requests_used == 2 and released.provider_requests_reserved == 0
        replay = await service.reserve_provider_request(
            tenant_id=tenant_id, operation_id=first_id, quantity=2, session=session
        )
        assert replay.replay and replay.reservation_id == first.reservation_id
        assert replay.status == "consumed" and replay.expires_at == first.expires_at
    async with sessions() as session:
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        rows = (await session.scalars(select(UsageReservation))).all()
        events = (await session.scalars(select(UsageEvent))).all()
        assert (
            entitlement.provider_requests_used == 2 and entitlement.provider_requests_reserved == 0
        )
        assert sorted(row.state for row in rows) == ["consumed", "released"]
        assert sorted(row.event_type for row in events) == ["consume", "release"]


async def test_expiry_and_caller_changes_survive_no_autoflush_reservation(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, now = await configured(sessions, tenant_id, limit=2, ttl=10)
    old_id, new_id = uuid4(), uuid4()
    old = await service.reserve_provider_request(
        tenant_id=tenant_id, operation_id=old_id, quantity=2
    )
    service.clock = lambda: now + timedelta(seconds=11)
    async with sessions.begin() as session:
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        entitlement.plan_code = "caller-pending-change"
        with session.no_autoflush:
            result = await service.reserve_provider_request(
                tenant_id=tenant_id, operation_id=new_id, quantity=2, session=session
            )
            assert result.provider_requests_reserved == entitlement.provider_requests_reserved == 2
            assert entitlement.plan_code == "caller-pending-change"
    async with sessions() as session:
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        expired = await session.get(UsageReservation, old.reservation_id)
        current = await session.get(UsageReservation, result.reservation_id)
        release = (await session.scalars(select(UsageEvent))).one()
        assert entitlement.plan_code == "caller-pending-change"
        assert (
            entitlement.provider_requests_reserved == 2 and entitlement.provider_requests_used == 0
        )
        assert expired.state == "released" and expired.released_at == service.clock()
        assert current.state == "reserved" and current.expires_at == service.clock() + timedelta(
            seconds=10
        )
        assert release.operation_id == old_id and release.quantity == 2
        assert release.event_type == "release" and release.source == "expiry"


@pytest.mark.parametrize("table", ["tenant_entitlements", "usage_reservations"])
async def test_failed_counter_or_reservation_write_rolls_back_both(billing_database, table):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, _ = await configured(sessions, tenant_id)
    operation_id = uuid4()
    constraint = "reject_batch_" + uuid4().hex
    column, value = (
        ("id", entitlement_id) if table == "tenant_entitlements" else ("operation_id", operation_id)
    )
    async with sessions.begin() as session:
        await session.execute(
            text(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"CHECK ({column} <> '{value}') NOT VALID"
            )
        )
    try:
        with pytest.raises(UsageError, match="usage_store_unavailable"):
            await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
        async with sessions() as session:
            assert not (await session.scalars(select(UsageReservation))).all()
            entitlement = await session.get(TenantEntitlement, entitlement_id)
            assert entitlement.provider_requests_reserved == entitlement.provider_requests_used == 0
    finally:
        async with sessions.begin() as session:
            await session.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"))
    recovered = await service.reserve_provider_request(
        tenant_id=tenant_id, operation_id=operation_id
    )
    assert recovered.provider_requests_reserved == 1


async def test_caller_rollback_discards_group_and_same_operation_can_retry(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, _ = await configured(sessions, tenant_id)
    operation_id = uuid4()
    with pytest.raises(RuntimeError, match="caller abort"):
        async with sessions.begin() as session:
            await service.reserve_provider_request(
                tenant_id=tenant_id, operation_id=operation_id, session=session
            )
            raise RuntimeError("caller abort")
    async with sessions() as session:
        assert not (await session.scalars(select(UsageReservation))).all()
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        assert entitlement.provider_requests_reserved == 0
    recovered = await service.reserve_provider_request(
        tenant_id=tenant_id, operation_id=operation_id
    )
    assert recovered.provider_requests_reserved == 1 and not recovered.replay


async def test_lost_commit_ack_replay_keeps_original_id_and_expiry(billing_database, monkeypatch):
    sessions, (tenant_id, _) = billing_database
    service, entitlement_id, now = await configured(sessions, tenant_id)
    operation_id = uuid4()
    dialect = sessions.kw["bind"].sync_engine.dialect
    original = dialect.do_commit
    injected = False

    def commit_then_fail(connection):
        nonlocal injected
        original(connection)
        if not injected:
            injected = True
            raise psycopg.OperationalError("synthetic lost commit acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(dialect, "do_commit", commit_then_fail)
        with pytest.raises(UsageError, match="usage_store_unavailable"):
            await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert injected
    async with sessions() as session:
        durable = (await session.scalars(select(UsageReservation))).one()
        durable_id = durable.id
    service.clock = lambda: now + timedelta(seconds=5)
    replay = await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert replay.replay and replay.reservation_id == durable_id
    assert replay.expires_at == now + timedelta(seconds=60)
    async with sessions() as session:
        assert len((await session.scalars(select(UsageReservation))).all()) == 1
        entitlement = await session.get(TenantEntitlement, entitlement_id)
        assert entitlement.provider_requests_reserved == 1


@pytest.mark.parametrize("same_key", [False, True])
async def test_concurrent_waiters_cannot_duplicate_or_overspend_last_slot(
    billing_database, same_key
):
    sessions, (tenant_id, _) = billing_database
    service, _, _ = await configured(sessions, tenant_id, limit=1)
    engine = sessions.kw["bind"].sync_engine
    operation_id = uuid4()
    pids = set()
    prefix = "reservation-race-" + uuid4().hex

    def capture(conn, cursor, statement, parameters, context, many):
        task = asyncio.current_task()
        if task and task.get_name().startswith(prefix):
            pids.add(conn.connection.driver_connection.info.backend_pid)

    tasks = []
    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with sessions.begin() as blocker:
            await blocker.execute(
                text("SELECT id FROM tenants WHERE id=:id FOR NO KEY UPDATE"), {"id": tenant_id}
            )
            tasks = [
                asyncio.create_task(
                    service.reserve_provider_request(
                        tenant_id=tenant_id, operation_id=operation_id if same_key else uuid4()
                    ),
                    name=f"{prefix}-{i}",
                )
                for i in range(3)
            ]
            async with asyncio.timeout(4):
                while True:
                    if len(pids) == 3:
                        async with sessions() as observer:
                            waiting = await observer.scalar(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE pid=ANY(:pids) AND wait_event_type='Lock'"
                                ),
                                {"pids": list(pids)},
                            )
                        if waiting == 3:
                            break
                    await asyncio.sleep(0.01)
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == (0 if same_key else 2)
    assert all(isinstance(r, UsageError) and r.code == "usage_limit_reached" for r in failures)
    if same_key:
        assert len({r.reservation_id for r in results}) == 1
        assert sum(r.replay for r in results) == 2
    async with sessions() as session:
        assert len((await session.scalars(select(UsageReservation))).all()) == 1
        entitlement = (await session.scalars(select(TenantEntitlement))).one()
        assert entitlement.provider_requests_reserved == 1
