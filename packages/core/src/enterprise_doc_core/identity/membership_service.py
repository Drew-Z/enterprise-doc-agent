from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.identity.models import (
    ExternalIdentityBinding,
    Membership,
    MembershipRole,
    Tenant,
    User,
)

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class MembershipAdministrationError(Exception):
    code = "membership_administration_error"


class MembershipAdministrationForbidden(MembershipAdministrationError):
    code = "membership_administration_forbidden"


class MembershipAdministrationInvalid(MembershipAdministrationError):
    code = "membership_administration_invalid"


class MembershipAdministrationNotFound(MembershipAdministrationError):
    code = "membership_administration_not_found"


class MembershipAdministrationConflict(MembershipAdministrationError):
    code = "membership_administration_conflict"


class MembershipLastOwnerRequired(MembershipAdministrationError):
    code = "membership_last_owner_required"


class MembershipSelfMutationForbidden(MembershipAdministrationError):
    code = "membership_self_mutation_forbidden"


@dataclass(frozen=True, slots=True)
class MembershipAdministrationResult:
    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MembershipAdministrationService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_members(
        self,
        *,
        tenant_id: UUID,
        role: str,
        query: str | None = None,
        limit: int = 100,
    ) -> tuple[MembershipAdministrationResult, ...]:
        self._require_owner(role)
        if limit < 1 or limit > 100:
            raise MembershipAdministrationInvalid()
        statement = (
            select(Membership, User.email)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id)
            .order_by(
                Membership.is_active.desc(),
                User.email.asc(),
                Membership.id.asc(),
            )
            .limit(limit)
        )
        normalized_query = query.strip() if query is not None else ""
        if normalized_query:
            escaped_query = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            statement = statement.where(User.email.ilike(f"%{escaped_query}%", escape="\\"))
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(self._result(membership, email=email) for membership, email in rows)

    async def provision_member(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        email: str,
        member_role: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MembershipAdministrationResult:
        self._require_owner(role)
        normalized_email = self._normalize_email(email)
        normalized_role = self._normalize_role(member_role)
        try:
            async with self.session_factory.begin() as session:
                await self._lock_active_tenant(session, tenant_id=tenant_id)
                user = await session.scalar(
                    select(User).where(func.lower(User.email) == normalized_email).with_for_update()
                )
                if user is None:
                    user = User(email=normalized_email)
                    session.add(user)
                    await session.flush()
                elif not user.is_active:
                    raise MembershipAdministrationConflict()

                membership = await session.scalar(
                    select(Membership)
                    .where(
                        Membership.tenant_id == tenant_id,
                        Membership.user_id == user.id,
                    )
                    .with_for_update()
                )
                action: str | None = None
                previous_role: str | None = None
                if membership is None:
                    membership = Membership(
                        tenant_id=tenant_id,
                        user_id=user.id,
                        role=normalized_role,
                        is_active=True,
                    )
                    session.add(membership)
                    action = "membership.provisioned"
                elif not membership.is_active:
                    previous_role = membership.role
                    membership.role = normalized_role
                    membership.is_active = True
                    action = "membership.reactivated"
                elif membership.role != normalized_role:
                    if membership.user_id == actor_id:
                        raise MembershipSelfMutationForbidden()
                    previous_role = membership.role
                    if membership.role == MembershipRole.OWNER.value:
                        await self._require_replacement_owner(session, tenant_id=tenant_id)
                    membership.role = normalized_role
                    action = "membership.role_changed"

                if action is not None:
                    await session.flush()
                    await session.refresh(
                        membership,
                        attribute_names=["created_at", "updated_at"],
                    )
                    await self._append_membership_event(
                        session,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        membership=membership,
                        email=normalized_email,
                        action=action,
                        previous_role=previous_role,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                return self._result(membership, email=normalized_email)
        except IntegrityError as error:
            raise MembershipAdministrationConflict() from error

    async def change_role(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        membership_id: UUID,
        member_role: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MembershipAdministrationResult:
        self._require_owner(role)
        normalized_role = self._normalize_role(member_role)
        async with self.session_factory.begin() as session:
            await self._lock_active_tenant(session, tenant_id=tenant_id)
            membership, email = await self._get_membership_for_update(
                session,
                tenant_id=tenant_id,
                membership_id=membership_id,
            )
            if membership.role == normalized_role:
                return self._result(membership, email=email)
            if membership.user_id == actor_id:
                raise MembershipSelfMutationForbidden()
            previous_role = membership.role
            if membership.is_active and previous_role == MembershipRole.OWNER.value:
                await self._require_replacement_owner(session, tenant_id=tenant_id)
            membership.role = normalized_role
            await session.flush()
            await session.refresh(membership, attribute_names=["updated_at"])
            await self._append_membership_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                membership=membership,
                email=email,
                action="membership.role_changed",
                previous_role=previous_role,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return self._result(membership, email=email)

    async def deactivate_member(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        membership_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MembershipAdministrationResult:
        self._require_owner(role)
        async with self.session_factory.begin() as session:
            await self._lock_active_tenant(session, tenant_id=tenant_id)
            membership, email = await self._get_membership_for_update(
                session,
                tenant_id=tenant_id,
                membership_id=membership_id,
            )
            if not membership.is_active:
                return self._result(membership, email=email)
            if membership.user_id == actor_id:
                raise MembershipSelfMutationForbidden()
            if membership.role == MembershipRole.OWNER.value:
                await self._require_replacement_owner(session, tenant_id=tenant_id)

            active_bindings = (
                await session.scalars(
                    select(ExternalIdentityBinding)
                    .where(
                        ExternalIdentityBinding.tenant_id == tenant_id,
                        ExternalIdentityBinding.user_id == membership.user_id,
                        ExternalIdentityBinding.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()
            membership.is_active = False
            for binding in active_bindings:
                binding.is_active = False
            await session.flush()
            await session.refresh(membership, attribute_names=["updated_at"])
            await self._append_membership_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                membership=membership,
                email=email,
                action="membership.deactivated",
                identity_bindings_deactivated=len(active_bindings),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return self._result(membership, email=email)

    async def activate_member(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        membership_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MembershipAdministrationResult:
        self._require_owner(role)
        async with self.session_factory.begin() as session:
            await self._lock_active_tenant(session, tenant_id=tenant_id)
            membership, email = await self._get_membership_for_update(
                session,
                tenant_id=tenant_id,
                membership_id=membership_id,
            )
            user_is_active = await session.scalar(
                select(User.is_active).where(User.id == membership.user_id)
            )
            if not user_is_active:
                raise MembershipAdministrationConflict()
            if membership.is_active:
                return self._result(membership, email=email)
            membership.is_active = True
            await session.flush()
            await session.refresh(membership, attribute_names=["updated_at"])
            await self._append_membership_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                membership=membership,
                email=email,
                action="membership.reactivated",
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return self._result(membership, email=email)

    @staticmethod
    async def _lock_active_tenant(session: AsyncSession, *, tenant_id: UUID) -> None:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
        if tenant is None or not tenant.is_active:
            raise MembershipAdministrationNotFound()

    @staticmethod
    async def _get_membership_for_update(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> tuple[Membership, str]:
        row = (
            await session.execute(
                select(Membership, User.email)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.id == membership_id,
                    Membership.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise MembershipAdministrationNotFound()
        membership, email = row
        return membership, str(email)

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
            raise MembershipLastOwnerRequired()

    @staticmethod
    async def _append_membership_event(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        membership: Membership,
        email: str,
        action: str,
        previous_role: str | None = None,
        identity_bindings_deactivated: int | None = None,
        request_id: str | None,
        correlation_id: str | None,
    ) -> None:
        metadata: dict[str, object] = {
            "user_id": str(membership.user_id),
            "email": email,
            "role": membership.role,
        }
        if previous_role is not None:
            metadata["previous_role"] = previous_role
        if identity_bindings_deactivated is not None:
            metadata["identity_bindings_deactivated"] = identity_bindings_deactivated
        await append_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="tenant_membership",
            resource_id=membership.id,
            metadata=metadata,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _normalize_email(value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) > 320 or _EMAIL_PATTERN.fullmatch(normalized) is None:
            raise MembershipAdministrationInvalid()
        return normalized

    @staticmethod
    def _normalize_role(value: str) -> str:
        if value not in {MembershipRole.OWNER.value, MembershipRole.MEMBER.value}:
            raise MembershipAdministrationInvalid()
        return value

    @staticmethod
    def _require_owner(role: str) -> None:
        if role != MembershipRole.OWNER.value:
            raise MembershipAdministrationForbidden()

    @staticmethod
    def _result(
        membership: Membership,
        *,
        email: str,
    ) -> MembershipAdministrationResult:
        return MembershipAdministrationResult(
            membership_id=membership.id,
            tenant_id=membership.tenant_id,
            user_id=membership.user_id,
            email=email,
            role=membership.role,
            is_active=membership.is_active,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )
