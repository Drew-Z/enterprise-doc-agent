import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.provider_calls import ProviderCallService, recorded_post
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import ProviderUsageSettings

pytestmark = pytest.mark.integration


async def no_op_guard(session):
    pass


async def post(client):
    return await recorded_post(
        client,
        "https://provider.test/v1/embeddings",
        provider="openai_compatible",
        model="embedding-test",
        json_body={},
        headers={},
        request_timeout=httpx.Timeout(1),
        require_metering=True,
    )


async def test_metered_http_commits_receipt_with_at_most_five_sql_roundtrips(billing_database):
    sessions, (tenant_id, _) = billing_database
    operation_id = uuid4()
    service = ProviderCallService(session_factory=sessions)
    engine = sessions.kw["bind"]
    observer = create_async_engine(engine.url)
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async def provider(request):
        # A different connection sees the committed intent before any HTTP work.
        async with observer.connect() as connection:
            assert (
                await connection.scalar(
                    select(ProviderDispatch.state).where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.operation_id == operation_id,
                    )
                )
                == "dispatched"
            )
        return httpx.Response(
            200,
            headers={"x-oneapi-request-id": "request-1"},
            json={"id": "response-1", "usage": {"total_tokens": 81}},
        )

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with service.scope(
                tenant_id=tenant_id, operation_id=operation_id, kind="query", guard=no_op_guard
            ):
                assert (await post(client)).status_code == 200
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
        await observer.dispose()
    async with sessions() as session:
        row = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).one()
        assert row.operation_id == operation_id and row.kind == "query"
        assert row.provider == "openai_compatible" and row.model == "embedding-test"
        assert (
            row.channel_hash == hashlib.sha256(b"https://provider.test:/v1/embeddings").hexdigest()
        )
        assert row.state == "responded" and row.status_code == 200
        assert row.provider_request_id == "request-1"
        assert row.provider_response_id == "response-1" and row.total_tokens == 81
        assert row.estimated_cost is None and row.currency is None
        assert row.finished_at >= row.started_at
    assert len(statements) <= 5, statements


def dispatch_scope(service, tenant_id, operation_id=None):
    return SimpleNamespace(
        service=service,
        tenant_id=tenant_id,
        operation_id=operation_id or uuid4(),
        kind="query",
        guard=no_op_guard,
    )


async def lock(session, key):
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


def day_key(tenant_id):
    return f"provider-day:{tenant_id}:{datetime.now(UTC).date()}"


async def wait_for_blocked(sessions, pids, expected):
    async with asyncio.timeout(4):
        while True:
            if len(pids) == expected:
                async with sessions() as session:
                    waiting = await session.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE pid = ANY(:pids) AND wait_event_type = 'Lock'"
                        ),
                        {"pids": list(pids)},
                    )
                if waiting == expected:
                    return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("budget", ["daily", "operation"])
async def test_last_budget_slot_after_real_day_lock_wait_is_not_overspent(billing_database, budget):
    sessions, (tenant_id, _) = billing_database
    settings = ProviderUsageSettings(
        daily_call_limit=2 if budget == "daily" else 100, query_call_limit=2
    )
    service = ProviderCallService(session_factory=sessions, settings=settings)
    operation_id = uuid4()
    requests, pids = [], []

    def provider(request):
        requests.append(request)
        return httpx.Response(200)

    async def guard(session):
        pids.append(await session.scalar(text("SELECT pg_backend_pid()")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with service.scope(
            tenant_id=tenant_id, operation_id=operation_id, kind="query", guard=no_op_guard
        ):
            await post(client)

        async def contender():
            with service.scope(
                tenant_id=tenant_id,
                operation_id=uuid4() if budget == "daily" else operation_id,
                kind="query",
                guard=guard,
            ):
                return await post(client)

        tasks = []
        try:
            async with sessions.begin() as blocker:
                await lock(blocker, day_key(tenant_id))
                tasks = [asyncio.create_task(contender()) for _ in range(3)]
                await wait_for_blocked(sessions, pids, 3)
                assert len(requests) == 1
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(result, httpx.Response) for result in results) == 1
    rejected = [result for result in results if isinstance(result, UsageError)]
    assert len(rejected) == 2
    assert all(str(result) == f"provider_{budget}_budget_exhausted" for result in rejected)
    assert len(requests) == 2
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).all()
        assert len(rows) == 2 and all(row.state == "responded" for row in rows)


async def test_budget_locks_wait_for_day_before_taking_operation(billing_database):
    sessions, (tenant_id, _) = billing_database
    service = ProviderCallService(session_factory=sessions)
    scope = dispatch_scope(service, tenant_id)
    pids = []

    async def guard(session):
        pids.append(await session.scalar(text("SELECT pg_backend_pid()")))

    scope.guard = guard
    task = None
    try:
        async with sessions.begin() as operation_blocker:
            await lock(
                operation_blocker, f"provider-operation:{tenant_id}:query:{scope.operation_id}"
            )
            async with sessions.begin() as day_blocker:
                await lock(day_blocker, day_key(tenant_id))
                task = asyncio.create_task(
                    service.begin(
                        scope, provider="test", model="test", endpoint="https://provider.test"
                    )
                )
                await wait_for_blocked(sessions, pids, 1)
                async with sessions() as observer:
                    locks = (
                        await observer.scalars(
                            text(
                                "SELECT granted FROM pg_locks "
                                "WHERE pid=:pid AND locktype='advisory'"
                            ),
                            {"pid": pids[0]},
                        )
                    ).all()
                    assert locks == [False]
            async with asyncio.timeout(4):
                while True:
                    async with sessions() as observer:
                        locks = (
                            await observer.scalars(
                                text(
                                    "SELECT granted FROM pg_locks "
                                    "WHERE pid=:pid AND locktype='advisory'"
                                ),
                                {"pid": pids[0]},
                            )
                        ).all()
                    if sorted(locks) == [False, True]:
                        break
                    await asyncio.sleep(0.01)
        receipt = await asyncio.wait_for(task, 5)
        await service.finish(scope, receipt, state="cancelled")
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def new_receipt(sessions, tenant_id):
    service = ProviderCallService(session_factory=sessions)
    scope = dispatch_scope(service, tenant_id)
    receipt = await service.begin(
        scope, provider="test", model="test", endpoint="https://provider.test"
    )
    return service, scope, receipt


async def receipt_metadata(sessions, receipt):
    async with sessions() as session:
        row = await session.get(ProviderDispatch, receipt)
        return (
            row.state,
            row.status_code,
            row.provider_request_id,
            row.provider_response_id,
            row.total_tokens,
            row.finished_at,
            row.estimated_cost,
            row.currency,
        )


async def test_concurrent_finishes_and_replay_keep_one_complete_terminal_receipt(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, scope, receipt = await new_receipt(sessions, tenant_id)
    engine = sessions.kw["bind"]
    pids = set()
    prefix = f"finish-{receipt}"

    def capture(conn, cursor, statement, parameters, context, executemany):
        task = asyncio.current_task()
        if task and task.get_name().startswith(prefix):
            pids.add(conn.connection.driver_connection.info.backend_pid)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    tasks = []
    try:
        async with sessions.begin() as blocker:
            await blocker.scalar(
                select(ProviderDispatch).where(ProviderDispatch.id == receipt).with_for_update()
            )
            tasks = [
                asyncio.create_task(
                    service.finish(
                        scope,
                        receipt,
                        state="responded",
                        response=httpx.Response(
                            200,
                            headers={"x-request-id": f"request-{i}"},
                            json={"id": f"response-{i}", "usage": {"total_tokens": i}},
                        ),
                    ),
                    name=f"{prefix}-{i}",
                )
                for i in range(3)
            ]
            await wait_for_blocked(sessions, pids, 3)
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    original = await receipt_metadata(sessions, receipt)
    winner = original[4]
    assert winner in range(3)
    assert original[:5] == ("responded", 200, f"request-{winner}", f"response-{winner}", winner)
    assert original[5] is not None and original[6:] == (None, None)
    await service.finish(scope, receipt, state="timeout")
    assert await receipt_metadata(sessions, receipt) == original


@pytest.mark.parametrize("mismatch", ["tenant", "operation", "receipt"])
async def test_finish_wrong_identity_is_missing_without_mutating_receipt(
    billing_database, mismatch
):
    sessions, (tenant_id, other_tenant_id) = billing_database
    service, scope, receipt = await new_receipt(sessions, tenant_id)
    original = await receipt_metadata(sessions, receipt)
    if mismatch == "tenant":
        scope.tenant_id = other_tenant_id
    elif mismatch == "operation":
        scope.operation_id = uuid4()
    with pytest.raises(UsageError, match="provider_usage_receipt_missing"):
        await service.finish(scope, uuid4() if mismatch == "receipt" else receipt, state="timeout")
    assert await receipt_metadata(sessions, receipt) == original


async def test_finish_constraint_failure_rolls_back_and_can_be_completed(billing_database):
    sessions, (tenant_id, _) = billing_database
    service, scope, receipt = await new_receipt(sessions, tenant_id)
    original = await receipt_metadata(sessions, receipt)
    with pytest.raises(UsageError, match="provider_usage_unavailable"):
        await service.finish(
            scope,
            receipt,
            state="invalid",
            response=httpx.Response(200, json={"usage": {"total_tokens": 8}}),
        )
    assert await receipt_metadata(sessions, receipt) == original
    await service.finish(scope, receipt, state="timeout")
    final = await receipt_metadata(sessions, receipt)
    assert final[0] == "timeout" and final[4] is None and final[5] is not None


async def test_finish_lost_commit_ack_replay_preserves_committed_metadata(
    billing_database, monkeypatch
):
    sessions, (tenant_id, _) = billing_database
    service, scope, receipt = await new_receipt(sessions, tenant_id)
    dialect = sessions.kw["bind"].sync_engine.dialect
    original_commit = dialect.do_commit
    injected = False

    def commit_then_lose_ack(connection):
        nonlocal injected
        original_commit(connection)
        if not injected:
            injected = True
            raise psycopg.OperationalError("synthetic lost commit acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(dialect, "do_commit", commit_then_lose_ack)
        with pytest.raises(UsageError, match="provider_usage_unavailable"):
            await service.finish(
                scope,
                receipt,
                state="responded",
                response=httpx.Response(
                    200,
                    headers={"x-request-id": "durable-request"},
                    json={"id": "durable-response", "usage": {"total_tokens": 13}},
                ),
            )
    assert injected
    original = await receipt_metadata(sessions, receipt)
    assert original[:5] == ("responded", 200, "durable-request", "durable-response", 13)
    await service.finish(scope, receipt, state="timeout")
    assert await receipt_metadata(sessions, receipt) == original
