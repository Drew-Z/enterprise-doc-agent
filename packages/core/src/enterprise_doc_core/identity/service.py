from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
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


class ExternalIdentityBindingError(Exception):
    code = "external_identity_binding_error"


class ExternalIdentityBindingForbidden(ExternalIdentityBindingError):
    code = "external_identity_binding_forbidden"


class ExternalIdentityBindingNotFound(ExternalIdentityBindingError):
    code = "external_identity_binding_not_found"


class ExternalIdentityBindingTargetNotFound(ExternalIdentityBindingError):
    code = "external_identity_binding_target_not_found"


class ExternalIdentityBindingConflict(ExternalIdentityBindingError):
    code = "external_identity_binding_conflict"


class ExternalIdentityBindingInvalid(ExternalIdentityBindingError):
    code = "external_identity_binding_invalid"


@dataclass(frozen=True, slots=True)
class ExternalIdentityBindingResult:
    binding_id: UUID
    tenant_id: UUID
    issuer: str
    subject: str
    user_id: UUID
    user_email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalIdentityMemberResult:
    user_id: UUID
    email: str
    role: str


class ExternalIdentityBindingService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_bindings(
        self,
        *,
        tenant_id: UUID,
        role: str,
    ) -> tuple[ExternalIdentityBindingResult, ...]:
        self._require_owner(role)
        statement = (
            select(ExternalIdentityBinding, User.email)
            .join(User, User.id == ExternalIdentityBinding.user_id)
            .where(ExternalIdentityBinding.tenant_id == tenant_id)
            .order_by(
                ExternalIdentityBinding.created_at.desc(),
                ExternalIdentityBinding.id.desc(),
            )
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(self._result(binding, user_email=email) for binding, email in rows)

    async def list_active_members(
        self,
        *,
        tenant_id: UUID,
        role: str,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[ExternalIdentityMemberResult, ...]:
        self._require_owner(role)
        if limit < 1 or limit > 50:
            raise ExternalIdentityBindingInvalid()
        statement = (
            select(User.id, User.email, Membership.role)
            .join(Membership, Membership.user_id == User.id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.is_active.is_(True),
                User.is_active.is_(True),
                Tenant.is_active.is_(True),
            )
            .order_by(User.email.asc(), User.id.asc())
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
        return tuple(
            ExternalIdentityMemberResult(user_id=user_id, email=email, role=member_role)
            for user_id, email, member_role in rows
        )

    async def create_binding(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        issuer: str,
        subject: str,
        user_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExternalIdentityBindingResult:
        self._require_owner(role)
        normalized_issuer = issuer.strip()
        normalized_subject = subject.strip()
        if (
            not normalized_issuer
            or not normalized_subject
            or len(normalized_issuer) > 512
            or len(normalized_subject) > 512
        ):
            raise ExternalIdentityBindingInvalid()
        try:
            async with self.session_factory.begin() as session:
                user_email = await session.scalar(
                    select(User.email)
                    .join(Membership, Membership.user_id == User.id)
                    .join(Tenant, Tenant.id == Membership.tenant_id)
                    .where(
                        User.id == user_id,
                        User.is_active.is_(True),
                        Membership.tenant_id == tenant_id,
                        Membership.is_active.is_(True),
                        Tenant.is_active.is_(True),
                    )
                )
                if user_email is None:
                    raise ExternalIdentityBindingTargetNotFound()
                binding = ExternalIdentityBinding(
                    tenant_id=tenant_id,
                    issuer=normalized_issuer,
                    subject=normalized_subject,
                    user_id=user_id,
                    is_active=True,
                )
                session.add(binding)
                await session.flush()
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="external_identity.binding.created",
                    resource_type="external_identity_binding",
                    resource_id=binding.id,
                    metadata={
                        "issuer": normalized_issuer,
                        "subject": normalized_subject,
                        "user_id": str(user_id),
                    },
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                return self._result(binding, user_email=str(user_email))
        except IntegrityError as error:
            raise ExternalIdentityBindingConflict() from error

    async def deactivate_binding(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        binding_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExternalIdentityBindingResult:
        self._require_owner(role)
        async with self.session_factory.begin() as session:
            row = (
                await session.execute(
                    select(ExternalIdentityBinding, User.email)
                    .join(User, User.id == ExternalIdentityBinding.user_id)
                    .where(
                        ExternalIdentityBinding.id == binding_id,
                        ExternalIdentityBinding.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise ExternalIdentityBindingNotFound()
            binding, user_email = row
            if binding.is_active:
                binding.is_active = False
                await session.flush()
                await session.refresh(binding, attribute_names=["updated_at"])
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="external_identity.binding.deactivated",
                    resource_type="external_identity_binding",
                    resource_id=binding.id,
                    metadata={
                        "issuer": binding.issuer,
                        "subject": binding.subject,
                        "user_id": str(binding.user_id),
                    },
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            return self._result(binding, user_email=str(user_email))

    async def activate_binding(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        binding_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExternalIdentityBindingResult:
        self._require_owner(role)
        async with self.session_factory.begin() as session:
            row = (
                await session.execute(
                    select(ExternalIdentityBinding, User.email)
                    .join(User, User.id == ExternalIdentityBinding.user_id)
                    .where(
                        ExternalIdentityBinding.id == binding_id,
                        ExternalIdentityBinding.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise ExternalIdentityBindingNotFound()
            binding, _ = row
            user_email = await session.scalar(
                select(User.email)
                .join(Membership, Membership.user_id == User.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    User.id == binding.user_id,
                    User.is_active.is_(True),
                    Membership.tenant_id == tenant_id,
                    Membership.is_active.is_(True),
                    Tenant.is_active.is_(True),
                )
            )
            if user_email is None:
                raise ExternalIdentityBindingTargetNotFound()
            if not binding.is_active:
                binding.is_active = True
                await session.flush()
                await session.refresh(binding, attribute_names=["updated_at"])
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="external_identity.binding.activated",
                    resource_type="external_identity_binding",
                    resource_id=binding.id,
                    metadata={
                        "issuer": binding.issuer,
                        "subject": binding.subject,
                        "user_id": str(binding.user_id),
                    },
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            return self._result(binding, user_email=str(user_email))

    @staticmethod
    def _require_owner(role: str) -> None:
        if role != MembershipRole.OWNER.value:
            raise ExternalIdentityBindingForbidden()

    @staticmethod
    def _result(
        binding: ExternalIdentityBinding,
        *,
        user_email: str,
    ) -> ExternalIdentityBindingResult:
        return ExternalIdentityBindingResult(
            binding_id=binding.id,
            tenant_id=binding.tenant_id,
            issuer=binding.issuer,
            subject=binding.subject,
            user_id=binding.user_id,
            user_email=user_email,
            is_active=binding.is_active,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )
