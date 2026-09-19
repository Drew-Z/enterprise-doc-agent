from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.browser_sessions.contracts import IssuedBrowserSession
from enterprise_doc_core.browser_sessions.errors import (
    BrowserContextStale,
    BrowserIdentityConflict,
    BrowserLoginInvalid,
    BrowserLoginRateLimited,
    BrowserPrincipalForbidden,
    BrowserSessionInvalid,
    BrowserSessionUnavailable,
)
from enterprise_doc_core.browser_sessions.models import (
    BrowserSession,
    BrowserSessionEvent,
)
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

from .conftest import BrowserDatabase
from .support import IDENTITY, ISSUER, RATE_KEY, seed_tenants, sign_in

pytestmark = pytest.mark.integration


async def test_login_cookie_and_state_are_one_use_and_expire(browser_db: BrowserDatabase) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    start = await service.begin_login(rate_key=RATE_KEY)
    with pytest.raises(BrowserLoginInvalid):
        await service.claim_login(state=start.state, verifier=SecretStr("x" * 43))
    claim = await service.claim_login(state=start.state, verifier=start.verifier)
    with pytest.raises(BrowserLoginInvalid):
        await service.claim_login(state=start.state, verifier=start.verifier)
    await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)
    with pytest.raises(BrowserLoginInvalid):
        await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)
    expired = await service.begin_login(rate_key=RATE_KEY)
    async with browser_db.sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE browser_login_attempts "
                "SET created_at=clock_timestamp()-interval '10 minutes', "
                "expires_at=clock_timestamp()-interval '1 second' WHERE consumed_at IS NULL"
            )
        )
    with pytest.raises(BrowserLoginInvalid):
        await service.claim_login(state=expired.state, verifier=expired.verifier)


async def test_concurrent_state_consumption_and_rate_limit(browser_db: BrowserDatabase) -> None:
    service = BrowserSessionService(
        session_factory=browser_db.sessions, trusted_issuer=ISSUER, login_limit_per_minute=1
    )
    results = await asyncio.gather(
        *(service.begin_login(rate_key=RATE_KEY) for _ in range(2)), return_exceptions=True
    )
    assert sum(isinstance(result, BrowserLoginRateLimited) for result in results) == 1
    start = next(result for result in results if not isinstance(result, BaseException))
    claims = await asyncio.gather(
        *(service.claim_login(state=start.state, verifier=start.verifier) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, BrowserLoginInvalid) for result in claims) == 1
    assert sum(not isinstance(result, BaseException) for result in claims) == 1


async def test_late_login_cannot_undo_logout_and_reauthentication_changes_context(
    browser_db: BrowserDatabase,
) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    first = await sign_in(service)
    second = await sign_in(service, first.credential)
    assert second.snapshot.generation == first.snapshot.generation + 1
    assert second.snapshot.session_id == first.snapshot.session_id
    with pytest.raises(BrowserSessionInvalid):
        await service.get_session(credential=first.credential)
    start = await service.begin_login(rate_key=RATE_KEY, previous_credential=second.credential)
    claim = await service.claim_login(
        state=start.state, verifier=start.verifier, previous_credential=second.credential
    )
    await service.logout(
        credential=second.credential, context_version=second.snapshot.context_version
    )
    with pytest.raises(BrowserLoginInvalid):
        await service.complete_login(
            attempt_id=claim.attempt_id, identity=IDENTITY, previous_credential=second.credential
        )
    third = await sign_in(service, second.credential)
    assert third.snapshot.generation == 1
    assert third.snapshot.session_id != second.snapshot.session_id
    assert third.snapshot.context_version != first.snapshot.context_version


async def test_selection_race_has_one_winner_and_stale_context_never_switches(
    browser_db: BrowserDatabase,
) -> None:
    _, first, second = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    original = await sign_in(service)
    results = await asyncio.gather(
        *(
            service.select_tenant(
                credential=original.credential,
                context_version=original.snapshot.context_version,
                tenant_id=tenant,
            )
            for tenant in (first, second)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, BrowserSessionInvalid) for result in results) == 1
    winner = next(result for result in results if isinstance(result, IssuedBrowserSession))
    with pytest.raises(BrowserContextStale):
        await service.authorize(
            credential=winner.credential, context_version=original.snapshot.context_version
        )
    with pytest.raises(BrowserContextStale):
        await service.select_tenant(
            credential=winner.credential,
            context_version=original.snapshot.context_version,
            tenant_id=second,
        )
    assert (
        await service.get_session(credential=winner.credential)
    ).selected == winner.snapshot.selected


@pytest.mark.parametrize("model", [Tenant, User, Membership, ExternalIdentityBinding])
async def test_inactive_entities_stop_existing_session(
    model: type, browser_db: BrowserDatabase
) -> None:
    _, tenant, _ = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    original = await sign_in(service)
    selected = await service.select_tenant(
        credential=original.credential,
        context_version=original.snapshot.context_version,
        tenant_id=tenant,
    )
    async with browser_db.sessions.begin() as session:
        await session.execute(update(model).values(is_active=False))
    with pytest.raises(BrowserPrincipalForbidden):
        await service.authorize(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
    assert (await service.get_session(credential=selected.credential)).selected is None
    assert (
        await service.list_tenants(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
        == ()
    )


async def test_deleted_and_recreated_binding_does_not_restore_old_selection(
    browser_db: BrowserDatabase,
) -> None:
    actor, tenant, _ = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    original = await sign_in(service)
    selected = await service.select_tenant(
        credential=original.credential,
        context_version=original.snapshot.context_version,
        tenant_id=tenant,
    )
    async with browser_db.sessions.begin() as session:
        await session.execute(
            delete(ExternalIdentityBinding).where(ExternalIdentityBinding.tenant_id == tenant)
        )
        session.add(
            ExternalIdentityBinding(
                tenant_id=tenant, user_id=actor, issuer=ISSUER, subject=IDENTITY.subject
            )
        )
    with pytest.raises(BrowserPrincipalForbidden):
        await service.authorize(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
    assert (
        len(
            await service.list_tenants(
                credential=selected.credential, context_version=selected.snapshot.context_version
            )
        )
        == 2
    )


async def test_role_is_live_and_identity_ambiguity_includes_inactive_binding(
    browser_db: BrowserDatabase,
) -> None:
    _, tenant, _ = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    original = await sign_in(service)
    selected = await service.select_tenant(
        credential=original.credential,
        context_version=original.snapshot.context_version,
        tenant_id=tenant,
    )
    async with browser_db.sessions.begin() as session:
        await session.execute(
            update(Membership).where(Membership.tenant_id == tenant).values(role="member")
        )
    assert (
        await service.authorize(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
    ).role == "member"
    async with browser_db.sessions.begin() as session:
        other_user, other_tenant = uuid4(), uuid4()
        session.add(User(id=other_user, email="different@example.test"))
        session.add(Tenant(id=other_tenant, name="企业丙", slug="tenant-c", quota_bytes=1000000))
        await session.flush()
        session.add(
            ExternalIdentityBinding(
                tenant_id=other_tenant,
                user_id=other_user,
                issuer=ISSUER,
                subject=IDENTITY.subject,
                is_active=False,
            )
        )
    with pytest.raises(BrowserIdentityConflict):
        await service.list_tenants(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
    with pytest.raises(BrowserIdentityConflict):
        await service.authorize(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )


async def test_email_alone_and_unselected_session_have_no_business_principal(
    browser_db: BrowserDatabase,
) -> None:
    async with browser_db.sessions.begin() as session:
        session.add(User(email=IDENTITY.email))
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    issued = await sign_in(service)
    assert (
        await service.list_tenants(
            credential=issued.credential, context_version=issued.snapshot.context_version
        )
        == ()
    )
    with pytest.raises(BrowserPrincipalForbidden):
        await service.authorize(
            credential=issued.credential, context_version=issued.snapshot.context_version
        )


async def test_session_expiry_checked_after_waiting_for_rotation_lock(
    browser_db: BrowserDatabase,
) -> None:
    _, tenant, _ = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    issued = await sign_in(service)
    task: asyncio.Task | None = None
    try:
        async with browser_db.sessions.begin() as blocker:
            row = await blocker.get(
                BrowserSession, issued.snapshot.session_id, with_for_update=True
            )
            assert row is not None
            now = await blocker.scalar(select(func.clock_timestamp()))
            row.expires_at = now + timedelta(seconds=0.3)
            await blocker.flush()
            pid = await blocker.scalar(select(func.pg_backend_pid()))
            task = asyncio.create_task(
                service.select_tenant(
                    credential=issued.credential,
                    context_version=issued.snapshot.context_version,
                    tenant_id=tenant,
                )
            )
            deadline = time.monotonic() + 3
            while True:
                waiting = await blocker.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted "
                        "AND :pid=ANY(pg_blocking_pids(pid)))"
                    ),
                    {"pid": pid},
                )
                if waiting:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.35)
            assert await blocker.scalar(select(func.clock_timestamp())) > row.expires_at
        with pytest.raises(BrowserSessionInvalid):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_audit_failure_rolls_back_rotation(browser_db: BrowserDatabase) -> None:
    _, tenant, _ = await seed_tenants(browser_db)
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    issued = await sign_in(service)
    async with browser_db.sessions.begin() as session:
        await session.execute(
            text(
                "ALTER TABLE browser_session_events ADD CONSTRAINT test_reject_selection "
                "CHECK(action <> 'selected')"
            )
        )
    with pytest.raises(BrowserSessionUnavailable):
        await service.select_tenant(
            credential=issued.credential,
            context_version=issued.snapshot.context_version,
            tenant_id=tenant,
        )
    snapshot = await service.get_session(credential=issued.credential)
    assert snapshot.generation == 1 and snapshot.selected is None
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BrowserSessionEvent)) == 1


@pytest.mark.parametrize(
    "identity",
    [
        VerifiedAdmissionIdentity(ISSUER, "subject-one", "owner@example.test", False),
        VerifiedAdmissionIdentity(
            "https://untrusted.test", "subject-one", "owner@example.test", True
        ),
    ],
)
async def test_core_rejects_untrusted_adapter_output(
    identity: VerifiedAdmissionIdentity, browser_db: BrowserDatabase
) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    start = await service.begin_login(rate_key=RATE_KEY)
    claim = await service.claim_login(state=start.state, verifier=start.verifier)
    with pytest.raises(BrowserLoginInvalid):
        await service.complete_login(attempt_id=claim.attempt_id, identity=identity)
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BrowserSession)) == 0
