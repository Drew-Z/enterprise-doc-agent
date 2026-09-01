from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.identity.models import (
    ExternalIdentityBinding,
    Membership,
    MembershipRole,
    Tenant,
    User,
)
from enterprise_doc_core.identity.scim_types import ScimUserPage, ScimUserResult

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ScimProvisioningError(Exception):
    code = "scim_provisioning_error"


class ScimProvisioningInvalid(ScimProvisioningError):
    code = "scim_provisioning_invalid"


class ScimProvisioningNotFound(ScimProvisioningError):
    code = "scim_provisioning_not_found"


class ScimProvisioningConflict(ScimProvisioningError):
    code = "scim_provisioning_conflict"


class ScimProvisioningService:
    """Apply one provider-normalized SCIM user projection atomically.

    The service deliberately accepts a normalized subject, email, role and
    active flag. Protocol parsing and token authentication stay in the API
    adapter so an actual IdP can be added without changing authorization rules.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_user(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        subject: str,
    ) -> ScimUserResult | None:
        normalized_issuer = issuer.strip()
        normalized_subject = subject.strip()
        if not normalized_issuer or len(normalized_issuer) > 512:
            raise ScimProvisioningInvalid()
        if not normalized_subject or len(normalized_subject) > 512:
            raise ScimProvisioningInvalid()

        async with self.session_factory() as session:
            tenant = await session.scalar(
                select(Tenant).where(
                    Tenant.id == tenant_id,
                    Tenant.is_active.is_(True),
                )
            )
            if tenant is None:
                return None
            binding = await session.scalar(
                select(ExternalIdentityBinding).where(
                    ExternalIdentityBinding.tenant_id == tenant_id,
                    ExternalIdentityBinding.issuer == normalized_issuer,
                    ExternalIdentityBinding.subject == normalized_subject,
                )
            )
            if binding is None:
                return None
            user = await session.scalar(select(User).where(User.id == binding.user_id))
            membership = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == binding.user_id,
                )
            )
            if user is None or membership is None:
                return None
            return self._result(
                tenant_id=tenant_id,
                user=user,
                membership=membership,
                binding=binding,
                subject=normalized_subject,
            )

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        start_index: int = 1,
        count: int = 100,
        user_name: str | None = None,
        external_id: str | None = None,
    ) -> ScimUserPage | None:
        normalized_issuer = issuer.strip()
        normalized_user_name = user_name.strip().lower() if user_name is not None else None
        normalized_external_id = external_id.strip() if external_id is not None else None
        if not normalized_issuer or len(normalized_issuer) > 512:
            raise ScimProvisioningInvalid()
        if not 1 <= start_index <= 1_000_000 or not 0 <= count <= 200:
            raise ScimProvisioningInvalid()
        if normalized_user_name is not None and (
            not normalized_user_name or len(normalized_user_name) > 320
        ):
            raise ScimProvisioningInvalid()
        if normalized_external_id is not None and (
            not normalized_external_id or len(normalized_external_id) > 512
        ):
            raise ScimProvisioningInvalid()

        async with self.session_factory() as session:
            tenant = await session.scalar(
                select(Tenant).where(
                    Tenant.id == tenant_id,
                    Tenant.is_active.is_(True),
                )
            )
            if tenant is None:
                return None

            filters = [
                ExternalIdentityBinding.tenant_id == tenant_id,
                ExternalIdentityBinding.issuer == normalized_issuer,
            ]
            if normalized_user_name is not None:
                filters.append(func.lower(User.email) == normalized_user_name)
            if normalized_external_id is not None:
                filters.append(ExternalIdentityBinding.subject == normalized_external_id)

            join_clause = (
                select(ExternalIdentityBinding, User, Membership)
                .join(User, User.id == ExternalIdentityBinding.user_id)
                .join(
                    Membership,
                    (Membership.tenant_id == tenant_id)
                    & (Membership.user_id == ExternalIdentityBinding.user_id),
                )
                .where(*filters)
            )
            total_results = int(
                await session.scalar(
                    select(func.count(ExternalIdentityBinding.id))
                    .select_from(ExternalIdentityBinding)
                    .join(User, User.id == ExternalIdentityBinding.user_id)
                    .join(
                        Membership,
                        (Membership.tenant_id == tenant_id)
                        & (Membership.user_id == ExternalIdentityBinding.user_id),
                    )
                    .where(*filters)
                )
                or 0
            )
            rows = (
                await session.execute(
                    join_clause.order_by(
                        ExternalIdentityBinding.created_at.asc(),
                        ExternalIdentityBinding.id.asc(),
                    )
                    .offset(start_index - 1)
                    .limit(count)
                )
            ).all()

        resources = tuple(
            self._result(
                tenant_id=tenant_id,
                user=user,
                membership=membership,
                binding=binding,
                subject=binding.subject,
            )
            for binding, user, membership in rows
        )
        return ScimUserPage(
            total_results=total_results,
            start_index=start_index,
            items_per_page=len(resources),
            resources=resources,
        )

    async def sync_user(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        subject: str,
        email: str | None,
        role: str | None,
        is_active: bool,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ScimUserResult | None:
        normalized_issuer = issuer.strip()
        normalized_subject = subject.strip()
        if not normalized_issuer or len(normalized_issuer) > 512:
            raise ScimProvisioningInvalid()
        if not normalized_subject or len(normalized_subject) > 512:
            raise ScimProvisioningInvalid()
        normalized_email = self._normalize_email(email) if email is not None else None
        if is_active and normalized_email is None:
            raise ScimProvisioningInvalid()
        normalized_role = self._normalize_role(role) if is_active else None

        async with self.session_factory.begin() as session:
            tenant = await session.scalar(
                select(Tenant).where(Tenant.id == tenant_id).with_for_update()
            )
            if tenant is None or not tenant.is_active:
                raise ScimProvisioningNotFound()

            binding = await session.scalar(
                select(ExternalIdentityBinding)
                .where(
                    ExternalIdentityBinding.tenant_id == tenant_id,
                    ExternalIdentityBinding.issuer == normalized_issuer,
                    ExternalIdentityBinding.subject == normalized_subject,
                )
                .with_for_update()
            )
            if binding is None and not is_active:
                return None

            user: User | None
            if binding is not None:
                user = await session.scalar(
                    select(User).where(User.id == binding.user_id).with_for_update()
                )
                if user is None:
                    raise ScimProvisioningConflict()
                email_changed = False
                if normalized_email is not None:
                    existing_user = await session.scalar(
                        select(User.id)
                        .where(
                            func.lower(User.email) == normalized_email,
                            User.id != user.id,
                        )
                        .with_for_update()
                    )
                    if existing_user is not None:
                        raise ScimProvisioningConflict()
                    email_changed = user.email != normalized_email
                    if email_changed:
                        user.email = normalized_email
            else:
                assert normalized_email is not None
                user = await session.scalar(
                    select(User).where(func.lower(User.email) == normalized_email).with_for_update()
                )
                if user is None:
                    user = User(email=normalized_email)
                    session.add(user)
                    await session.flush()
                elif not user.is_active:
                    raise ScimProvisioningConflict()
                email_changed = False

            if not user.is_active:
                raise ScimProvisioningConflict()

            membership = await session.scalar(
                select(Membership)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == user.id,
                )
                .with_for_update()
            )

            if not is_active:
                if binding is None or membership is None:
                    return None
                if membership.is_active and membership.role == MembershipRole.OWNER.value:
                    await self._require_replacement_owner(session, tenant_id=tenant_id)
                membership_was_active = membership.is_active
                binding_was_active = binding.is_active
                membership.is_active = False
                binding.is_active = False
                await session.flush()
                await session.refresh(user, attribute_names=["updated_at"])
                await session.refresh(membership, attribute_names=["updated_at"])
                await session.refresh(binding, attribute_names=["updated_at"])
                if membership_was_active or binding_was_active:
                    await self._append_event(
                        session,
                        tenant_id=tenant_id,
                        membership_id=membership.id,
                        action="scim.user.deprovisioned",
                        issuer=normalized_issuer,
                        subject=normalized_subject,
                        email=user.email,
                        role=membership.role,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                return self._result(
                    tenant_id=tenant_id,
                    user=user,
                    membership=membership,
                    binding=binding,
                    subject=normalized_subject,
                )

            if normalized_role is None:
                raise ScimProvisioningInvalid()
            role_value = normalized_role
            membership_action: str | None = None
            if membership is None:
                membership = Membership(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=role_value,
                    is_active=True,
                )
                session.add(membership)
                await session.flush()
                membership_action = "scim.user.provisioned"
            else:
                if not membership.is_active:
                    membership.is_active = True
                    membership_action = "scim.user.reactivated"
                if membership.role != role_value:
                    if (
                        membership.is_active
                        and membership.role == MembershipRole.OWNER.value
                        and role_value == MembershipRole.MEMBER.value
                    ):
                        await self._require_replacement_owner(session, tenant_id=tenant_id)
                    membership.role = role_value
                    membership_action = "scim.user.role_changed"

            binding_created = False
            if binding is None:
                binding = ExternalIdentityBinding(
                    tenant_id=tenant_id,
                    issuer=normalized_issuer,
                    subject=normalized_subject,
                    user_id=user.id,
                    is_active=True,
                )
                session.add(binding)
                await session.flush()
                binding_created = True
            elif binding.user_id != user.id:
                raise ScimProvisioningConflict()
            else:
                binding_was_active = binding.is_active
                binding.is_active = True
                if not binding_was_active and membership_action is None:
                    membership_action = "scim.user.reactivated"

            await session.flush()
            await session.refresh(user, attribute_names=["updated_at"])
            await session.refresh(membership, attribute_names=["created_at", "updated_at"])
            await session.refresh(binding, attribute_names=["created_at", "updated_at"])
            if membership_action is not None or email_changed or binding_created:
                event_action = membership_action or (
                    "scim.user.provisioned" if binding_created else "scim.user.updated"
                )
                await self._append_event(
                    session,
                    tenant_id=tenant_id,
                    membership_id=membership.id,
                    action=event_action,
                    issuer=normalized_issuer,
                    subject=normalized_subject,
                    email=user.email,
                    role=membership.role,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            return self._result(
                tenant_id=tenant_id,
                user=user,
                membership=membership,
                binding=binding,
                subject=normalized_subject,
            )

    @staticmethod
    async def _require_replacement_owner(session: AsyncSession, *, tenant_id: UUID) -> None:
        owner_count = await session.scalar(
            select(func.count(Membership.id)).where(
                Membership.tenant_id == tenant_id,
                Membership.role == MembershipRole.OWNER.value,
                Membership.is_active.is_(True),
            )
        )
        if owner_count is None or owner_count <= 1:
            raise ScimProvisioningConflict()

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        membership_id: UUID,
        action: str,
        issuer: str,
        subject: str,
        email: str,
        role: str,
        request_id: str | None,
        correlation_id: str | None,
    ) -> None:
        await append_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=None,
            action=action,
            resource_type="tenant_membership",
            resource_id=membership_id,
            metadata={
                "source": "scim",
                "issuer": issuer,
                "subject": subject,
                "email": email,
                "role": role,
            },
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _normalize_email(value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) > 320 or _EMAIL_PATTERN.fullmatch(normalized) is None:
            raise ScimProvisioningInvalid()
        return normalized

    @staticmethod
    def _normalize_role(value: str | None) -> str:
        if value not in {MembershipRole.OWNER.value, MembershipRole.MEMBER.value}:
            raise ScimProvisioningInvalid()
        return value

    @staticmethod
    def _result(
        *,
        tenant_id: UUID,
        user: User,
        membership: Membership,
        binding: ExternalIdentityBinding,
        subject: str,
    ) -> ScimUserResult:
        return ScimUserResult(
            tenant_id=tenant_id,
            user_id=user.id,
            membership_id=membership.id,
            binding_id=binding.id,
            subject=subject,
            email=user.email,
            role=membership.role,
            is_active=user.is_active and membership.is_active and binding.is_active,
            last_modified=max(user.updated_at, membership.updated_at, binding.updated_at),
        )
