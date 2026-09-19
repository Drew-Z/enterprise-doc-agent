from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.invitations.contracts import CreateInvitation, InvitationOperation
from enterprise_doc_core.invitations.errors import InvitationAccountConflict, InvitationDenied
from enterprise_doc_core.invitations.models import MembershipInvitation

from .conftest import InvitationDatabase
from .support import ISSUER, invitation_service, tenant_owner

pytestmark = pytest.mark.integration


async def test_verified_recipient_inspects_then_accepts_once(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "colleague", "colleague@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    preview = await service.inspect(token=issued.token, identity=identity)
    assert preview.tenant_name == "邀请测试企业" and preview.state == "pending"
    async with invitation_db.sessions() as session:
        for model in (User, Membership, ExternalIdentityBinding):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
    receipt = await service.accept(
        token=issued.token,
        identity=identity,
        request_id="accept-request",
        correlation_id="join-flow",
    )
    replay = await service.accept(token=issued.token, identity=identity)
    assert not receipt.replayed and replay.replayed
    assert replay.membership_id == receipt.membership_id
    assert receipt.tenant_id == owner.tenant_id and receipt.tenant_name == preview.tenant_name
    async with invitation_db.sessions() as session:
        for model in (User, Membership, ExternalIdentityBinding):
            assert await session.scalar(select(func.count()).select_from(model)) == 2
        member = await session.get(Membership, receipt.membership_id)
        assert member is not None and member.role == "member" and member.is_active
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "membership_invitation.accepted",
                )
            )
        ).all()
        assert len(events) == 1
        assert events[0].request_id == "accept-request"
        assert events[0].correlation_id == "join-flow"


@pytest.mark.parametrize(
    "fault",
    [
        "email",
        "issuer",
        "unverified",
        "malformed",
        "revoked",
        "rotated",
        "expired",
        "owner",
        "tenant",
    ],
)
async def test_invalid_invitation_never_creates_access(
    invitation_db: InvitationDatabase,
    fault: str,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "recipient", "recipient@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    token = issued.token
    if fault == "email":
        identity = VerifiedAdmissionIdentity(ISSUER, "recipient", "wrong@example.test", True)
    elif fault == "issuer":
        identity = VerifiedAdmissionIdentity(
            "https://other.example.test", "recipient", identity.email, True
        )
    elif fault == "unverified":
        identity = VerifiedAdmissionIdentity(ISSUER, "recipient", identity.email, False)
    elif fault == "malformed":
        token = SecretStr("invalid")
    elif fault in {"revoked", "rotated"}:
        action = service.revoke if fault == "revoked" else service.regenerate
        await action(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            invitation_id=issued.invitation.invitation_id,
            request=InvitationOperation(expected_generation=1, operation_id=uuid4()),
        )
    else:
        async with invitation_db.sessions.begin() as session:
            if fault == "owner":
                member = await session.get(Membership, owner.membership_id)
                assert member is not None
                member.role = "member"
            elif fault == "tenant":
                tenant = await session.get(Tenant, owner.tenant_id)
                assert tenant is not None
                tenant.is_active = False
            else:
                invitation = await session.get(
                    MembershipInvitation, issued.invitation.invitation_id
                )
                assert invitation is not None
                invitation.created_at = datetime.now(UTC) - timedelta(days=2)
                invitation.issued_at = invitation.created_at
                invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for action in (service.inspect, service.accept):
        with pytest.raises(InvitationDenied):
            await action(token=token, identity=identity)
    async with invitation_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Membership)) == 1


async def test_same_email_does_not_link_a_different_subject(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    target = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    issued = await service.create(
        tenant_id=target.tenant_id,
        actor_id=target.user_id,
        request=CreateInvitation(email=owner.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    wrong = VerifiedAdmissionIdentity(ISSUER, "same-email-new-subject", owner.email, True)
    for action in (service.inspect, service.accept):
        with pytest.raises(InvitationAccountConflict):
            await action(token=issued.token, identity=wrong)
    correct = VerifiedAdmissionIdentity(ISSUER, owner.subject, owner.email, True)
    receipt = await service.accept(token=issued.token, identity=correct)
    async with invitation_db.sessions() as session:
        member = await session.get(Membership, receipt.membership_id)
        assert member is not None and member.user_id == owner.user_id
        assert await session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.parametrize("inactive", ["user", "membership", "binding", "orphan_binding"])
async def test_invitation_does_not_restore_inactive_or_orphaned_identity(
    invitation_db: InvitationDatabase,
    inactive: str,
) -> None:
    source = await tenant_owner(invitation_db)
    target = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    issued = await service.create(
        tenant_id=target.tenant_id,
        actor_id=target.user_id,
        request=CreateInvitation(email=source.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    identity = VerifiedAdmissionIdentity(ISSUER, source.subject, source.email, True)
    async with invitation_db.sessions.begin() as session:
        if inactive == "user":
            user = await session.get(User, source.user_id)
            assert user is not None
            user.is_active = False
        else:
            if inactive != "orphan_binding":
                session.add(
                    Membership(
                        tenant_id=target.tenant_id,
                        user_id=source.user_id,
                        role="member",
                        is_active=inactive != "membership",
                    )
                )
            session.add(
                ExternalIdentityBinding(
                    tenant_id=target.tenant_id,
                    user_id=source.user_id,
                    issuer=ISSUER,
                    subject=source.subject,
                    is_active=inactive != "binding",
                )
            )
    with pytest.raises(InvitationAccountConflict):
        await service.accept(token=issued.token, identity=identity)
    async with invitation_db.sessions() as session:
        invitation = await session.get(MembershipInvitation, issued.invitation.invitation_id)
        assert invitation is not None and invitation.state == "pending"


@pytest.mark.parametrize(
    "changed", ["membership", "binding", "user", "tenant", "deleted_membership", "deleted_binding"]
)
async def test_consumed_receipt_cannot_restore_changed_entities(
    invitation_db: InvitationDatabase,
    changed: str,
) -> None:
    owner = await tenant_owner(invitation_db)
    service = invitation_service(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "consumed", "consumed@example.test", True)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    receipt = await service.accept(token=issued.token, identity=identity)
    async with invitation_db.sessions.begin() as session:
        member = await session.get(Membership, receipt.membership_id)
        assert member is not None
        binding = await session.scalar(
            select(ExternalIdentityBinding).where(
                ExternalIdentityBinding.user_id == member.user_id,
            )
        )
        assert binding is not None
        if changed == "deleted_membership":
            user_id = member.user_id
            await session.execute(delete(Membership).where(Membership.id == member.id))
            session.add(Membership(tenant_id=owner.tenant_id, user_id=user_id, role="member"))
        elif changed == "deleted_binding":
            await session.execute(
                delete(ExternalIdentityBinding).where(ExternalIdentityBinding.id == binding.id)
            )
            session.add(
                ExternalIdentityBinding(
                    tenant_id=owner.tenant_id,
                    user_id=member.user_id,
                    issuer=ISSUER,
                    subject=identity.subject,
                )
            )
        elif changed == "membership":
            member.is_active = False
        elif changed == "binding":
            binding.is_active = False
        elif changed == "user":
            user = await session.get(User, member.user_id)
            assert user is not None
            user.is_active = False
        else:
            tenant = await session.get(Tenant, owner.tenant_id)
            assert tenant is not None
            tenant.is_active = False
    with pytest.raises(InvitationDenied):
        await service.accept(token=issued.token, identity=identity)


async def test_existing_active_member_does_not_consume_another_seat_or_change_role(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    service = invitation_service(invitation_db)
    issued = await service.create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=owner.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    identity = VerifiedAdmissionIdentity(ISSUER, owner.subject, owner.email, True)
    receipt = await service.accept(token=issued.token, identity=identity)
    assert receipt.membership_id == owner.membership_id
    assert (await service.accept(token=issued.token, identity=identity)).replayed
    async with invitation_db.sessions() as session:
        member = await session.get(Membership, owner.membership_id)
        assert member is not None and member.role == "owner"
        assert await session.scalar(select(func.count()).select_from(Membership)) == 1
