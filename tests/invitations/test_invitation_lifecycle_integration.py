from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, User
from enterprise_doc_core.invitations.contracts import (
    CreateInvitation,
    InvitationOperation,
    InvitationSettings,
    invitation_digest,
)
from enterprise_doc_core.invitations.errors import (
    InvitationConflict,
    InvitationForbidden,
    InvitationIneligible,
    InvitationLimitReached,
    InvitationNotFound,
    InvitationUnavailable,
)
from enterprise_doc_core.invitations.models import MembershipInvitation, MembershipInvitationEvent
from enterprise_doc_core.invitations.service import MembershipInvitationService

from .conftest import InvitationDatabase
from .support import ISSUER, invitation_service, tenant_owner

pytestmark = pytest.mark.integration


async def test_invitation_create_is_idempotent_without_provisioning(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = MembershipInvitationService(
        session_factory=invitation_db.sessions,
        settings=InvitationSettings(enabled=True),
        trusted_issuer=ISSUER,
    )
    request = CreateInvitation(email="  COLLEAGUE@example.test ", operation_id=uuid4())
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=request,
        request_id="create-request",
        correlation_id="invitation-flow",
    )
    replay = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=request,
    )
    assert issued.token is not None
    assert len(issued.token.get_secret_value()) == 48
    assert issued.token.get_secret_value() not in repr(issued)
    assert issued.invitation.email == "colleague@example.test"
    assert issued.invitation.state == "pending"
    assert not issued.replayed
    assert replay.replayed and replay.token is None
    assert replay.invitation == issued.invitation
    async with invitation_db.sessions() as session:
        for model in (User, Membership, ExternalIdentityBinding):
            assert await session.scalar(select(func.count()).select_from(model)) == 1


async def test_regeneration_and_revoke_are_fenced_and_do_not_replay_secrets(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    first = await service.create(
        **args,
        request=CreateInvitation(email="rotate@example.test", operation_id=uuid4()),
    )
    operation = InvitationOperation(expected_generation=1, operation_id=uuid4())
    rotated = await service.regenerate(
        **args,
        invitation_id=first.invitation.invitation_id,
        request=operation,
    )
    assert rotated.invitation.generation == 2
    assert rotated.token is not None and first.token is not None
    assert invitation_digest(rotated.token) != invitation_digest(first.token)
    replay = await service.regenerate(
        **args,
        invitation_id=first.invitation.invitation_id,
        request=operation,
    )
    assert replay.replayed and replay.token is None
    with pytest.raises(InvitationConflict):
        await service.revoke(
            **args,
            invitation_id=first.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        )
    revoke_op = InvitationOperation(expected_generation=2, operation_id=uuid4())
    revoked = await service.revoke(
        **args,
        invitation_id=first.invitation.invitation_id,
        request=revoke_op,
    )
    assert revoked.invitation.state == "revoked" and revoked.token is None
    replay_after_revoke = await service.regenerate(
        **args,
        invitation_id=first.invitation.invitation_id,
        request=operation,
    )
    assert replay_after_revoke.replayed and replay_after_revoke.token is None
    assert replay_after_revoke.invitation.state == "revoked"
    assert (
        await service.revoke(
            **args,
            invitation_id=first.invitation.invitation_id,
            request=revoke_op,
        )
    ).replayed


async def test_list_reports_real_seats_without_link_secret(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=2)
    service = invitation_service(invitation_db)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email="listed@example.test", operation_id=uuid4()),
    )
    result = await service.list_invitations(tenant_id=owner.tenant_id, actor_id=owner.user_id)
    assert result.items == (issued.invitation,)
    assert result.eligible and not result.has_more
    assert (result.seats.active, result.seats.limit, result.seats.remaining) == (1, 2, 1)
    assert "token" not in asdict(result.items[0])
    assert "token_digest" not in asdict(result.items[0])


async def test_management_rechecks_owner_and_tenant_scope(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    other = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    request = CreateInvitation(email="scoped@example.test", operation_id=uuid4())
    for actor in (other.user_id, uuid4()):
        with pytest.raises(InvitationForbidden):
            await service.create(tenant_id=owner.tenant_id, actor_id=actor, request=request)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=request,
    )
    with pytest.raises(InvitationNotFound):
        await service.revoke(
            tenant_id=other.tenant_id,
            actor_id=other.user_id,
            invitation_id=issued.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        )
    async with invitation_db.sessions.begin() as session:
        member = await session.get(Membership, owner.membership_id)
        assert member is not None
        member.role = "member"
    with pytest.raises(InvitationForbidden):
        await service.list_invitations(tenant_id=owner.tenant_id, actor_id=owner.user_id)


async def test_creation_rejects_changed_operation_and_pending_duplicate(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    operation = uuid4()
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    await service.create(
        **args, request=CreateInvitation(email="one@example.test", operation_id=operation)
    )
    for request in (
        CreateInvitation(email="two@example.test", operation_id=operation),
        CreateInvitation(email="ONE@example.test", operation_id=uuid4()),
    ):
        with pytest.raises(InvitationConflict):
            await service.create(**args, request=request)
    assert len((await service.list_invitations(**args)).items) == 1


async def test_legacy_enterprise_and_disabled_feature_are_not_invitation_eligible(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=None)
    service = invitation_service(invitation_db)
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    state = await service.list_invitations(**args)
    assert not state.eligible and state.seats.limit is None and state.seats.remaining is None
    request = CreateInvitation(email="legacy@example.test", operation_id=uuid4())
    with pytest.raises(InvitationIneligible):
        await service.create(**args, request=request)
    disabled = MembershipInvitationService(
        session_factory=invitation_db.sessions,
        settings=InvitationSettings(),
        trusted_issuer=ISSUER,
    )
    with pytest.raises(InvitationUnavailable):
        await disabled.create(**args, request=request)


async def test_regeneration_rejects_a_changed_platform_issuer(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email="provider-change@example.test", operation_id=uuid4()),
    )
    changed = MembershipInvitationService(
        session_factory=invitation_db.sessions,
        settings=InvitationSettings(enabled=True),
        trusted_issuer="https://replacement.example.test",
    )
    with pytest.raises(InvitationConflict):
        await changed.regenerate(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            invitation_id=issued.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        )


async def test_operation_event_requires_a_request_digest(invitation_db: InvitationDatabase) -> None:
    owner = await tenant_owner(invitation_db)
    issued = await invitation_service(invitation_db).create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email="event-check@example.test", operation_id=uuid4()),
    )
    with pytest.raises(IntegrityError):
        async with invitation_db.sessions.begin() as session:
            session.add(
                MembershipInvitationEvent(
                    invitation_id=issued.invitation.invitation_id,
                    tenant_id=owner.tenant_id,
                    actor_id=owner.user_id,
                    generation=2,
                    action="regenerated",
                    operation_id=uuid4(),
                    request_digest=None,
                    occurred_at=datetime.now(UTC),
                )
            )


async def test_pending_capacity_includes_expired_links_and_list_keeps_all_pending_visible(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    issued = [
        await service.create(
            **args,
            request=CreateInvitation(email=f"capacity-{index}@example.test", operation_id=uuid4()),
        )
        for index in range(100)
    ]
    # Move issuance events out of the rolling hour so the pending cap is tested independently.
    async with invitation_db.sessions.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        await session.execute(
            update(MembershipInvitationEvent).values(occurred_at=now - timedelta(hours=2))
        )
        await session.execute(
            update(MembershipInvitation)
            .where(MembershipInvitation.id == issued[0].invitation.invitation_id)
            .values(
                created_at=now - timedelta(hours=3),
                issued_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
                expires_at=now - timedelta(hours=1),
            )
        )
    with pytest.raises(InvitationLimitReached):
        await service.create(
            **args,
            request=CreateInvitation(email="over-capacity@example.test", operation_id=uuid4()),
        )
    before = await service.list_invitations(**args)
    assert len(before.items) == 100 and not before.has_more
    assert sum(item.state == "expired" for item in before.items) == 1
    await service.revoke(
        **args,
        invitation_id=issued[1].invitation.invitation_id,
        request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
    )
    added = await service.create(
        **args, request=CreateInvitation(email="after-revoke@example.test", operation_id=uuid4())
    )
    after = await service.list_invitations(**args)
    assert after.has_more and len(after.items) == 100
    assert all(item.state in {"pending", "expired"} for item in after.items)
    assert added.invitation.invitation_id in {item.invitation_id for item in after.items}
    assert issued[0].invitation.invitation_id in {item.invitation_id for item in after.items}


async def test_rolling_issuance_limit_counts_regenerations_but_allows_replay_and_revoke(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    args = {"tenant_id": owner.tenant_id, "actor_id": owner.user_id}
    current = await service.create(
        **args, request=CreateInvitation(email="hourly@example.test", operation_id=uuid4())
    )
    for _ in range(99):
        operation = InvitationOperation(
            expected_generation=current.invitation.generation, operation_id=uuid4()
        )
        current = await service.regenerate(
            **args, invitation_id=current.invitation.invitation_id, request=operation
        )
    assert current.invitation.generation == 100
    replay = await service.regenerate(
        **args, invitation_id=current.invitation.invitation_id, request=operation
    )
    assert replay.replayed and replay.token is None
    with pytest.raises(InvitationLimitReached):
        await service.regenerate(
            **args,
            invitation_id=current.invitation.invitation_id,
            request=InvitationOperation(expected_generation=100, operation_id=uuid4()),
        )
    await service.revoke(
        **args,
        invitation_id=current.invitation.invitation_id,
        request=InvitationOperation(expected_generation=100, operation_id=uuid4()),
    )
    request = CreateInvitation(email="next-hour@example.test", operation_id=uuid4())
    with pytest.raises(InvitationLimitReached):
        await service.create(**args, request=request)
    async with invitation_db.sessions.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        await session.execute(
            update(MembershipInvitationEvent).values(occurred_at=now - timedelta(hours=2))
        )
    assert (await service.create(**args, request=request)).invitation.state == "pending"
