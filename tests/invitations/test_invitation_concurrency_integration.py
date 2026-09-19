from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from enterprise_doc_api.auth.bootstrap import bootstrap_principal
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity, value_digest
from enterprise_doc_core.identity.membership_service import MembershipAdministrationService
from enterprise_doc_core.identity.models import (
    Membership,
    MembershipRole,
    Tenant,
    User,
)
from enterprise_doc_core.identity.scim_service import ScimProvisioningService
from enterprise_doc_core.identity.seats import MembershipSeatLimitReached
from enterprise_doc_core.invitations.contracts import CreateInvitation, InvitationOperation
from enterprise_doc_core.invitations.errors import InvitationConflict, InvitationDenied
from enterprise_doc_core.invitations.models import MembershipInvitation, MembershipInvitationEvent

from .conftest import InvitationDatabase
from .support import ISSUER, invitation_service, tenant_owner

pytestmark = pytest.mark.integration


async def test_same_invitation_accepts_once_under_concurrency(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "racing", "racing@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    results = await asyncio.gather(
        *(service.accept(token=issued.token, identity=identity) for _ in range(8))
    )
    assert sum(not result.replayed for result in results) == 1
    assert len({result.membership_id for result in results}) == 1
    async with invitation_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Membership)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MembershipInvitationEvent)
                .where(
                    MembershipInvitationEvent.action == "accepted",
                )
            )
            == 1
        )


@pytest.mark.parametrize("writer", ["invitation", "provision", "activate", "scim", "bootstrap"])
async def test_last_seat_serializes_invitation_and_existing_writers(
    invitation_db: InvitationDatabase,
    writer: str,
) -> None:
    owner = await tenant_owner(invitation_db, seats=2)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "last-seat-a", "last-seat-a@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    member_service = MembershipAdministrationService(session_factory=invitation_db.sessions)
    second_email = "last-seat-b@example.test"
    member_id, user_id = uuid4(), uuid4()
    if writer == "activate":
        async with invitation_db.sessions.begin() as session:
            session.add(User(id=user_id, email=second_email))
            await session.flush()
            session.add(
                Membership(
                    id=member_id,
                    tenant_id=owner.tenant_id,
                    user_id=user_id,
                    role="member",
                    is_active=False,
                )
            )
    second_token = None
    if writer == "invitation":
        second = await service.create(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            request=CreateInvitation(email=second_email, operation_id=uuid4()),
        )
        second_token = second.token

    async def competitor() -> object:
        if writer == "invitation":
            assert second_token is not None
            return await service.accept(
                token=second_token,
                identity=VerifiedAdmissionIdentity(ISSUER, "last-seat-b", second_email, True),
            )
        if writer == "provision":
            return await member_service.provision_member(
                tenant_id=owner.tenant_id,
                actor_id=owner.user_id,
                role="owner",
                email=second_email,
                member_role="member",
            )
        if writer == "activate":
            return await member_service.activate_member(
                tenant_id=owner.tenant_id,
                actor_id=owner.user_id,
                role="owner",
                membership_id=member_id,
            )
        if writer == "scim":
            return await ScimProvisioningService(session_factory=invitation_db.sessions).sync_user(
                tenant_id=owner.tenant_id,
                issuer=ISSUER,
                subject="last-seat-b",
                email=second_email,
                role="member",
                is_active=True,
            )
        return await bootstrap_principal(
            settings=ApiSettings(),
            session_factory=invitation_db.sessions,
            tenant_name="邀请测试企业",
            tenant_slug=f"t-{owner.tenant_id.hex}",
            email=second_email,
            role=MembershipRole.MEMBER,
            quota_bytes=1000000,
        )

    results = await asyncio.gather(
        service.accept(token=issued.token, identity=identity),
        competitor(),
        return_exceptions=True,
    )
    assert sum(isinstance(result, MembershipSeatLimitReached) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with invitation_db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count(Membership.id)).where(
                    Membership.tenant_id == owner.tenant_id,
                    Membership.is_active.is_(True),
                )
            )
            == 2
        )


async def test_accept_revoke_regenerate_have_one_legal_winner(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(
        ISSUER, "lifecycle-race", "lifecycle-race@example.test", True
    )
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    issued = await service.create(
        **args,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    results = await asyncio.gather(
        service.accept(token=issued.token, identity=identity),
        service.regenerate(
            **args,
            invitation_id=issued.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        ),
        service.revoke(
            **args,
            invitation_id=issued.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert (
        sum(isinstance(result, (InvitationConflict, InvitationDenied)) for result in results) == 2
    )
    async with invitation_db.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(MembershipInvitationEvent)) == 2
        )


async def _wait_for_lock(db: InvitationDatabase) -> None:
    deadline = monotonic() + 4
    while monotonic() < deadline:
        async with db.sessions() as session:
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name=:name AND wait_event_type='Lock' "
                    "AND pid <> pg_backend_pid()"
                ),
                {"name": db.schema},
            )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("The acceptance did not enter the expected real database lock wait")


@pytest.mark.parametrize("lock", ["tenant", "identity"])
async def test_expiry_is_rechecked_after_real_lock_wait(
    invitation_db: InvitationDatabase,
    lock: str,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "expiry-wait", "expiry-wait@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    expiry = datetime.now(UTC) + timedelta(seconds=1)
    async with invitation_db.sessions.begin() as session:
        invitation = await session.get(MembershipInvitation, issued.invitation.invitation_id)
        assert invitation is not None
        invitation.expires_at = expiry
    task = None
    try:
        async with invitation_db.sessions.begin() as holder:
            if lock == "tenant":
                await holder.execute(
                    select(Tenant).where(Tenant.id == owner.tenant_id).with_for_update()
                )
            else:
                await holder.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"admission:email:{value_digest(identity.email)}"},
                )
            task = asyncio.create_task(service.accept(token=issued.token, identity=identity))
            await _wait_for_lock(invitation_db)
            remaining = (
                expiry - await holder.scalar(select(func.clock_timestamp()))
            ).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining + 0.02)
            assert await holder.scalar(select(func.clock_timestamp())) >= expiry
        with pytest.raises(InvitationDenied):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with invitation_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Membership)) == 1
