from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.admission.contracts import AdmissionReceipt
from enterprise_doc_core.admission.errors import (
    AdmissionAccountConflict,
    AdmissionBusy,
    AdmissionDenied,
)
from enterprise_doc_core.admission.models import TenantAdmissionGrant, TenantInitialEntitlement
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

_MAX_BINDINGS = 1000


async def resolve_admission_user(
    session: AsyncSession, *, issuer: str, subject: str, email: str
) -> User:
    """Resolve explicit identity evidence without reviving or modifying existing accounts."""
    binding_query = select(ExternalIdentityBinding).where(
        ExternalIdentityBinding.issuer == issuer, ExternalIdentityBinding.subject == subject
    )
    before = (
        await session.execute(
            select(
                ExternalIdentityBinding.id,
                ExternalIdentityBinding.tenant_id,
                ExternalIdentityBinding.user_id,
            )
            .where(
                ExternalIdentityBinding.issuer == issuer, ExternalIdentityBinding.subject == subject
            )
            .order_by(ExternalIdentityBinding.id)
            .limit(_MAX_BINDINGS + 1)
        )
    ).all()
    email_users = tuple(
        await session.scalars(select(User.id).where(func.lower(User.email) == email).limit(2))
    )
    if not before:
        if email_users:
            raise AdmissionAccountConflict()
        new_user = User(id=uuid4(), email=email, is_active=True)
        session.add(new_user)
        return new_user
    user_ids = {row.user_id for row in before}
    if len(before) > _MAX_BINDINGS or len(user_ids) != 1 or set(email_users) != user_ids:
        raise AdmissionAccountConflict()
    user_id = next(iter(user_ids))
    tenant_ids = {row.tenant_id for row in before}
    # Existing governance locks Tenant before User. NOWAIT also avoids cycles with
    # legacy joined binding locks whose row acquisition order is planner-dependent.
    tenants = {
        tenant.id: tenant
        for tenant in await session.scalars(
            select(Tenant)
            .where(Tenant.id.in_(tenant_ids))
            .order_by(Tenant.id)
            .with_for_update(nowait=True)
        )
    }
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update(nowait=True))
    if (
        user is None
        or not user.is_active
        or user.email.lower() != email
        or set(tenants) != tenant_ids
    ):
        raise AdmissionAccountConflict()
    members = tuple(
        await session.scalars(
            select(Membership)
            .where(Membership.user_id == user_id, Membership.tenant_id.in_(tenant_ids))
            .order_by(Membership.id)
            .with_for_update(nowait=True)
        )
    )
    bindings = tuple(
        await session.scalars(
            binding_query.order_by(ExternalIdentityBinding.id)
            .limit(_MAX_BINDINGS + 1)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        )
    )
    after = [(binding.id, binding.tenant_id, binding.user_id) for binding in bindings]
    if after != [tuple(row) for row in before]:
        raise AdmissionBusy()
    active_tenants = {tenant_id for tenant_id, tenant in tenants.items() if tenant.is_active}
    active_memberships = {member.tenant_id for member in members if member.is_active}
    if not any(
        binding.is_active and binding.tenant_id in active_tenants & active_memberships
        for binding in bindings
    ):
        raise AdmissionAccountConflict()
    return user


async def replay_admission(
    session: AsyncSession,
    *,
    grant: TenantAdmissionGrant,
    identity_digest: str,
    request_digest: str,
    issuer: str,
    subject: str,
    email: str,
) -> AdmissionReceipt:
    if (
        grant.accepted_identity_digest != identity_digest
        or grant.accepted_request_digest != request_digest
        or grant.accepted_tenant_id is None
        or grant.accepted_user_id is None
        or grant.accepted_membership_id is None
        or grant.accepted_binding_id is None
        or grant.accepted_entitlement_id is None
        or grant.accepted_at is None
    ):
        raise AdmissionDenied()
    tenant = await session.scalar(
        select(Tenant).where(Tenant.id == grant.accepted_tenant_id).with_for_update(nowait=True)
    )
    if tenant is None or not tenant.is_active or tenant.slug != f"tenant-{tenant.id.hex}":
        raise AdmissionDenied()
    user = await session.scalar(
        select(User).where(User.id == grant.accepted_user_id).with_for_update(nowait=True)
    )
    if user is None or not user.is_active or user.email.lower() != email:
        raise AdmissionDenied()
    member = await session.scalar(
        select(Membership)
        .where(Membership.id == grant.accepted_membership_id)
        .with_for_update(nowait=True)
    )
    if (
        member is None
        or not member.is_active
        or member.role != "owner"
        or member.tenant_id != tenant.id
        or member.user_id != user.id
    ):
        raise AdmissionDenied()
    binding = await session.scalar(
        select(ExternalIdentityBinding)
        .where(ExternalIdentityBinding.id == grant.accepted_binding_id)
        .with_for_update(nowait=True)
    )
    if (
        binding is None
        or not binding.is_active
        or binding.tenant_id != tenant.id
        or binding.user_id != user.id
        or binding.issuer != issuer
        or binding.subject != subject
    ):
        raise AdmissionDenied()
    entitlement = await session.scalar(
        select(TenantInitialEntitlement)
        .where(TenantInitialEntitlement.id == grant.accepted_entitlement_id)
        .with_for_update(nowait=True)
    )
    if (
        entitlement is None
        or entitlement.tenant_id != tenant.id
        or entitlement.grant_id != grant.id
        or entitlement.quota_bytes != grant.quota_bytes
        or entitlement.seat_limit != grant.seat_limit
    ):
        raise AdmissionDenied()
    return AdmissionReceipt(
        grant_id=grant.id,
        tenant_id=tenant.id,
        user_id=user.id,
        membership_id=member.id,
        binding_id=binding.id,
        entitlement_id=entitlement.id,
        tenant_slug=tenant.slug,
        accepted_at=grant.accepted_at,
        replayed=True,
    )
