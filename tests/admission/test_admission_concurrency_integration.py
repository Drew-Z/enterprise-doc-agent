from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from enterprise_doc_core.admission.contracts import AdmissionReceipt, prepare_admission_credential
from enterprise_doc_core.admission.errors import (
    AdmissionAccountConflict,
    AdmissionBusy,
    AdmissionConflict,
    AdmissionDenied,
)
from enterprise_doc_core.admission.models import TenantAdmissionEvent, TenantAdmissionGrant
from enterprise_doc_core.identity.models import Tenant, User
from tests.admission.conftest import AdmissionDatabase
from tests.admission.test_admission_acceptance_integration import IDENTITY
from tests.admission.test_admission_identity_integration import first_account
from tests.admission.test_admission_lifecycle_integration import (
    OPERATOR,
    grant_request,
    service_for,
)

pytestmark = pytest.mark.integration


async def wait_for_blocked(database: AdmissionDatabase, blocker: int, minimum: int = 1) -> None:
    async with asyncio.timeout(5):
        while True:
            async with database.engine.connect() as connection:
                blocked = await connection.scalar(
                    text(
                        "WITH RECURSIVE blocked(pid) AS ("
                        "SELECT CAST(:blocker AS integer) UNION "
                        "SELECT activity.pid FROM pg_stat_activity activity "
                        "JOIN blocked parent ON parent.pid = ANY(pg_blocking_pids(activity.pid))) "
                        "SELECT count(*) - 1 FROM blocked"
                    ),
                    {"blocker": blocker},
                )
                if blocked >= minimum:
                    return
            await asyncio.sleep(0.01)


async def test_concurrent_acceptance_consumes_once(admission_db: AdmissionDatabase) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    receipts = await asyncio.gather(
        *(
            service.accept(
                token=credential.token, identity=IDENTITY, tenant_name="Concurrent company"
            )
            for _ in range(8)
        )
    )
    assert len({receipt.tenant_id for receipt in receipts}) == 1
    assert sum(not receipt.replayed for receipt in receipts) == 1
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 2


@pytest.mark.parametrize("same_subject", [True, False])
async def test_different_grants_coordinate_identity_and_email(
    admission_db: AdmissionDatabase, same_subject: bool
) -> None:
    service = service_for(admission_db)
    credentials = [prepare_admission_credential(), prepare_admission_credential()]
    for credential in credentials:
        await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    results = await asyncio.gather(
        service.accept(token=credentials[0].token, identity=IDENTITY, tenant_name="One"),
        service.accept(
            token=credentials[1].token,
            identity=IDENTITY if same_subject else replace(IDENTITY, subject="different"),
            tenant_name="Two",
        ),
        return_exceptions=True,
    )
    if same_subject:
        assert all(isinstance(result, AdmissionReceipt) for result in results)
        assert results[0].user_id == results[1].user_id
    else:
        assert sum(isinstance(result, AdmissionReceipt) for result in results) == 1
        assert sum(isinstance(result, AdmissionAccountConflict) for result in results) == 1
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Tenant)) == (
            2 if same_subject else 1
        )


@pytest.mark.parametrize("first", ["accept", "revoke"])
async def test_accept_and_revoke_have_one_terminal_outcome(
    admission_db: AdmissionDatabase, first: str
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    tasks: dict[str, asyncio.Task[object]] = {}
    try:
        async with admission_db.sessions.begin() as locker:
            await locker.scalar(
                select(TenantAdmissionGrant)
                .where(TenantAdmissionGrant.id == credential.grant_id)
                .with_for_update()
            )
            blocker = await locker.scalar(select(func.pg_backend_pid()))
            for operation in (first, "revoke" if first == "accept" else "accept"):
                call = (
                    service.accept(token=credential.token, identity=IDENTITY, tenant_name="Race")
                    if operation == "accept"
                    else service.revoke(operator=OPERATOR, grant_id=credential.grant_id)
                )
                tasks[operation] = asyncio.create_task(call)
                await wait_for_blocked(admission_db, blocker, len(tasks))
        results = dict(
            zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True), strict=True)
        )
        state = (await service.show(operator=OPERATOR, grant_id=credential.grant_id)).state
        assert state == ("accepted" if first == "accept" else "revoked")
        if state == "accepted":
            assert isinstance(results["accept"], AdmissionReceipt)
            assert isinstance(results["revoke"], AdmissionConflict)
        else:
            assert isinstance(results["accept"], AdmissionDenied)
        async with admission_db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Tenant)) == (
                1 if state == "accepted" else 0
            )
            assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 2
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def test_expiry_is_checked_after_waiting_for_grant_lock(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    accepting: asyncio.Task[AdmissionReceipt] | None = None
    try:
        async with admission_db.sessions.begin() as locker:
            grant = await locker.scalar(
                select(TenantAdmissionGrant)
                .where(TenantAdmissionGrant.id == credential.grant_id)
                .with_for_update()
            )
            assert grant is not None
            clock = await locker.scalar(select(func.clock_timestamp()))
            grant.expires_at = clock + timedelta(seconds=1)
            await locker.flush()
            blocker = await locker.scalar(select(func.pg_backend_pid()))
            accepting = asyncio.create_task(
                service.accept(
                    token=credential.token, identity=IDENTITY, tenant_name="Expired while queued"
                )
            )
            await wait_for_blocked(admission_db, blocker)
            async with admission_db.engine.connect() as observer:
                started = await observer.scalar(
                    text(
                        "SELECT min(xact_start) FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid))"
                    ),
                    {"blocker": blocker},
                )
                assert started is not None and started < grant.expires_at
            now = await locker.scalar(select(func.clock_timestamp()))
            await asyncio.sleep(max(0, (grant.expires_at - now).total_seconds()) + 0.01)
            assert await locker.scalar(select(func.clock_timestamp())) > grant.expires_at
        with pytest.raises(AdmissionDenied):
            await accepting
        assert (
            await service.show(operator=OPERATOR, grant_id=credential.grant_id)
        ).state == "expired"
        async with admission_db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Tenant)) == 0
    finally:
        if accepting is not None:
            if not accepting.done():
                accepting.cancel()
            await asyncio.gather(accepting, return_exceptions=True)


async def test_busy_existing_account_requires_explicit_retry(
    admission_db: AdmissionDatabase,
) -> None:
    previous = await first_account(admission_db)
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    async with admission_db.sessions.begin() as governance:
        await governance.scalar(select(User).where(User.id == previous.user_id).with_for_update())
        with pytest.raises(AdmissionBusy):
            await service.accept(
                token=credential.token, identity=IDENTITY, tenant_name="Retry explicitly"
            )
        assert (
            await service.show(operator=OPERATOR, grant_id=credential.grant_id)
        ).state == "pending"
    receipt = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Retry explicitly"
    )
    assert receipt.user_id == previous.user_id


async def test_consumed_receipt_still_replays_after_original_expiry(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    original = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Durable receipt"
    )
    async with admission_db.sessions.begin() as session:
        grant = await session.get(TenantAdmissionGrant, credential.grant_id)
        assert grant is not None and grant.accepted_at is not None
        grant.expires_at = grant.accepted_at + timedelta(microseconds=1)
    replay = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Durable receipt"
    )
    assert replay.replayed and replay.tenant_id == original.tenant_id
