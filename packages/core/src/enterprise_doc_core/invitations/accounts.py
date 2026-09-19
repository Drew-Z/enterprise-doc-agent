from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.admission.accounts import resolve_admission_user
from enterprise_doc_core.admission.contracts import (
    VerifiedAdmissionIdentity,
    exact_identity_value,
    normalized_email,
    value_digest,
)
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.invitations.contracts import InvitationReceipt
from enterprise_doc_core.invitations.errors import InvitationAccountConflict, InvitationDenied
from enterprise_doc_core.invitations.models import MembershipInvitation


def verified_recipient(
    identity: VerifiedAdmissionIdentity,
    trusted_issuer: str,
) -> VerifiedAdmissionIdentity:
    if not isinstance(identity, VerifiedAdmissionIdentity) or identity.email_verified is not True:
        raise InvitationDenied()
    try:
        issuer = exact_identity_value(identity.issuer)
        subject = exact_identity_value(identity.subject)
        email = normalized_email(identity.email)
    except (ValueError, TypeError, AttributeError):
        raise InvitationDenied() from None
    if issuer != trusted_issuer:
        raise InvitationDenied()
    return VerifiedAdmissionIdentity(issuer, subject, email, True)


async def invitation_account(session: AsyncSession, identity: VerifiedAdmissionIdentity) -> User:
    # Share admission's account-creation serialization without imposing new global
    # binding uniqueness on legacy tenant-scoped identity administrators.
    keys = sorted(
        (
            f"admission:email:{value_digest(identity.email)}",
            f"admission:identity:{value_digest(identity.issuer, identity.subject)}",
        )
    )
    for key in keys:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )
    return await resolve_admission_user(
        session,
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
    )


async def invitation_relationships(
    session: AsyncSession,
    tenant_id: UUID,
    user: User,
    identity: VerifiedAdmissionIdentity,
) -> tuple[Membership | None, ExternalIdentityBinding | None]:
    member = await session.scalar(
        select(Membership)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user.id,
        )
        .with_for_update(nowait=True)
    )
    binding = await session.scalar(
        select(ExternalIdentityBinding)
        .where(
            ExternalIdentityBinding.tenant_id == tenant_id,
            ExternalIdentityBinding.issuer == identity.issuer,
            ExternalIdentityBinding.subject == identity.subject,
        )
        .with_for_update(nowait=True)
    )
    if member is not None and not member.is_active:
        raise InvitationAccountConflict()
    if binding is not None and (
        not binding.is_active or binding.user_id != user.id or member is None
    ):
        raise InvitationAccountConflict()
    return member, binding


async def replay_invitation(
    session: AsyncSession,
    invitation: MembershipInvitation,
    tenant: Tenant,
    identity: VerifiedAdmissionIdentity,
) -> InvitationReceipt:
    if invitation.accepted_identity_digest != value_digest(
        identity.issuer,
        identity.subject,
        identity.email,
    ) or any(
        value is None
        for value in (
            invitation.accepted_user_id,
            invitation.accepted_membership_id,
            invitation.accepted_binding_id,
            invitation.accepted_at,
        )
    ):
        raise InvitationDenied()
    user = await session.scalar(
        select(User)
        .where(
            User.id == invitation.accepted_user_id,
        )
        .with_for_update(nowait=True)
    )
    member = await session.scalar(
        select(Membership)
        .where(
            Membership.id == invitation.accepted_membership_id,
        )
        .with_for_update(nowait=True)
    )
    binding = await session.scalar(
        select(ExternalIdentityBinding)
        .where(
            ExternalIdentityBinding.id == invitation.accepted_binding_id,
        )
        .with_for_update(nowait=True)
    )
    if (
        user is None
        or not user.is_active
        or user.email.lower() != identity.email
        or member is None
        or not member.is_active
        or member.tenant_id != tenant.id
        or member.user_id != user.id
        or binding is None
        or not binding.is_active
        or binding.tenant_id != tenant.id
        or binding.user_id != user.id
        or binding.issuer != identity.issuer
        or binding.subject != identity.subject
    ):
        raise InvitationDenied()
    return InvitationReceipt(tenant.id, tenant.name, member.id, True)
