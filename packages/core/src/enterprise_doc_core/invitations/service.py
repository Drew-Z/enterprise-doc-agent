from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.admission.contracts import (
    VerifiedAdmissionIdentity,
    exact_identity_value,
    value_digest,
)
from enterprise_doc_core.admission.errors import AdmissionAccountConflict, AdmissionBusy
from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.identity.seats import ensure_membership_capacity, membership_seats
from enterprise_doc_core.invitations.accounts import (
    invitation_account,
    invitation_relationships,
    replay_invitation,
    verified_recipient,
)
from enterprise_doc_core.invitations.contracts import (
    CreateInvitation,
    InvitationList,
    InvitationMutation,
    InvitationOperation,
    InvitationPreview,
    InvitationReceipt,
    InvitationSettings,
    InvitationSnapshot,
    InvitationState,
    invitation_digest,
    prepare_invitation_token,
)
from enterprise_doc_core.invitations.errors import (
    InvitationAccountConflict,
    InvitationBusy,
    InvitationConflict,
    InvitationDenied,
    InvitationForbidden,
    InvitationIneligible,
    InvitationLimitReached,
    InvitationNotFound,
    InvitationUnavailable,
)
from enterprise_doc_core.invitations.models import MembershipInvitation, MembershipInvitationEvent

MAX_PENDING = 100
MAX_ISSUANCE_PER_HOUR = 100


class MembershipInvitationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: InvitationSettings,
        trusted_issuer: str,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.trusted_issuer = exact_identity_value(trusted_issuer)

    async def list_invitations(self, *, tenant_id: UUID, actor_id: UUID) -> InvitationList:
        async with self._transaction() as session:
            await self._tenant(session, tenant_id)
            await self._owner(session, tenant_id, actor_id)
            rows = (
                await session.scalars(
                    select(MembershipInvitation)
                    .where(MembershipInvitation.tenant_id == tenant_id)
                    .order_by(
                        case((MembershipInvitation.state == "pending", 0), else_=1),
                        MembershipInvitation.created_at.desc(),
                        MembershipInvitation.id.desc(),
                    )
                    .limit(101)
                )
            ).all()
            seats = await membership_seats(session, tenant_id)
            now = await self._now(session)
            return InvitationList(
                tuple(self._snapshot(row, now) for row in rows[:100]),
                len(rows) > 100,
                seats,
                seats.limit is not None,
            )

    async def create(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        request: CreateInvitation,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> InvitationMutation:
        digest = value_digest("issued", request.email, self.trusted_issuer, str(actor_id))
        async with self._transaction() as session:
            await self._tenant(session, tenant_id)
            owner = await self._owner(session, tenant_id, actor_id)
            replay = await self._replay(session, tenant_id, request.operation_id, digest)
            if replay is not None:
                return replay
            if (await membership_seats(session, tenant_id)).limit is None:
                raise InvitationIneligible()
            now = await self._now(session)
            await self._issuance_limit(session, tenant_id, now)
            pending = await session.scalar(
                select(func.count(MembershipInvitation.id)).where(
                    MembershipInvitation.tenant_id == tenant_id,
                    MembershipInvitation.state == "pending",
                )
            )
            if (pending or 0) >= MAX_PENDING:
                raise InvitationLimitReached()
            token = prepare_invitation_token()
            invitation = MembershipInvitation(
                id=uuid4(),
                tenant_id=tenant_id,
                email=request.email,
                issuer=self.trusted_issuer,
                issued_by_user_id=actor_id,
                issued_by_membership_id=owner.id,
                token_digest=invitation_digest(token),
                generation=1,
                state="pending",
                created_at=now,
                issued_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self.settings.ttl_seconds),
            )
            session.add(invitation)
            await session.flush()
            await self._event(
                session,
                invitation,
                action="issued",
                actor_id=actor_id,
                now=now,
                operation_id=request.operation_id,
                request_digest=digest,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return InvitationMutation(self._snapshot(invitation, now), False, token)

    async def inspect(
        self,
        *,
        token: SecretStr,
        identity: VerifiedAdmissionIdentity,
    ) -> InvitationPreview:
        identity = verified_recipient(identity, self.trusted_issuer)
        digest = invitation_digest(token)
        async with self._transaction(accepting=True) as session:
            invitation, tenant = await self._credential(session, digest, identity)
            if invitation.state == "accepted":
                await replay_invitation(session, invitation, tenant, identity)
            else:
                await self._pending(session, invitation)
                user = await invitation_account(session, identity)
                # resolve_admission_user stages a new User without flushing it.
                # Inspection must leave no account or membership behind.
                if user in session.new:
                    session.expunge(user)
                await invitation_relationships(session, tenant.id, user, identity)
                await self._pending(session, invitation)
            return InvitationPreview(
                tenant.name,
                invitation.expires_at,
                self._snapshot(invitation, await self._now(session)).state,
            )

    async def accept(
        self,
        *,
        token: SecretStr,
        identity: VerifiedAdmissionIdentity,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> InvitationReceipt:
        identity = verified_recipient(identity, self.trusted_issuer)
        digest = invitation_digest(token)
        async with self._transaction(accepting=True) as session:
            invitation, tenant = await self._credential(session, digest, identity)
            if invitation.state == "accepted":
                return await replay_invitation(session, invitation, tenant, identity)
            await self._pending(session, invitation)
            user = await invitation_account(session, identity)
            member, binding = await invitation_relationships(session, tenant.id, user, identity)
            if member is None:
                await ensure_membership_capacity(session, tenant.id)
            now = await self._now(session)
            if now >= invitation.expires_at:
                raise InvitationDenied()
            if member is None:
                member = Membership(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role="member",
                    is_active=True,
                )
                session.add(member)
            if binding is None:
                binding = ExternalIdentityBinding(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    user_id=user.id,
                    issuer=identity.issuer,
                    subject=identity.subject,
                    is_active=True,
                )
                session.add(binding)
            invitation.state = "accepted"
            invitation.accepted_at = now
            invitation.updated_at = now
            invitation.accepted_user_id = user.id
            invitation.accepted_membership_id = member.id
            invitation.accepted_binding_id = binding.id
            invitation.accepted_identity_digest = value_digest(
                identity.issuer,
                identity.subject,
                identity.email,
            )
            await session.flush()
            await self._event(
                session,
                invitation,
                action="accepted",
                actor_id=user.id,
                now=now,
                operation_id=None,
                request_digest=None,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return InvitationReceipt(tenant.id, tenant.name, member.id, False)

    async def _credential(
        self,
        session: AsyncSession,
        digest: str,
        identity: VerifiedAdmissionIdentity,
    ) -> tuple[MembershipInvitation, Tenant]:
        location = (
            await session.execute(
                select(
                    MembershipInvitation.id,
                    MembershipInvitation.tenant_id,
                ).where(MembershipInvitation.token_digest == digest)
            )
        ).one_or_none()
        if location is None:
            raise InvitationDenied()
        try:
            tenant = await self._tenant(session, location.tenant_id)
            invitation = await self._invitation(session, tenant.id, location.id)
        except InvitationNotFound:
            raise InvitationDenied() from None
        if (
            invitation.token_digest != digest
            or invitation.issuer != identity.issuer
            or invitation.email != identity.email
        ):
            raise InvitationDenied()
        return invitation, tenant

    async def _pending(self, session: AsyncSession, invitation: MembershipInvitation) -> None:
        if invitation.state != "pending" or await self._now(session) >= invitation.expires_at:
            raise InvitationDenied()
        try:
            owner = await self._owner(session, invitation.tenant_id, invitation.issued_by_user_id)
        except InvitationForbidden:
            raise InvitationDenied() from None
        if owner.id != invitation.issued_by_membership_id:
            raise InvitationDenied()
        if (await membership_seats(session, invitation.tenant_id)).limit is None:
            raise InvitationIneligible()

    async def regenerate(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        invitation_id: UUID,
        request: InvitationOperation,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> InvitationMutation:
        return await self._change(
            tenant_id=tenant_id,
            actor_id=actor_id,
            invitation_id=invitation_id,
            request=request,
            action="regenerated",
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def revoke(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        invitation_id: UUID,
        request: InvitationOperation,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> InvitationMutation:
        return await self._change(
            tenant_id=tenant_id,
            actor_id=actor_id,
            invitation_id=invitation_id,
            request=request,
            action="revoked",
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def _change(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        invitation_id: UUID,
        request: InvitationOperation,
        action: Literal["regenerated", "revoked"],
        request_id: str | None,
        correlation_id: str | None,
    ) -> InvitationMutation:
        digest = value_digest(
            action,
            str(invitation_id),
            str(request.expected_generation),
            str(actor_id),
        )
        async with self._transaction() as session:
            await self._tenant(session, tenant_id)
            owner = await self._owner(session, tenant_id, actor_id)
            replay = await self._replay(session, tenant_id, request.operation_id, digest)
            if replay is not None:
                return replay
            invitation = await self._invitation(session, tenant_id, invitation_id)
            if (
                invitation.state != "pending"
                or invitation.generation != request.expected_generation
            ):
                raise InvitationConflict()
            now = await self._now(session)
            token = None
            if action == "regenerated":
                if invitation.issuer != self.trusted_issuer:
                    raise InvitationConflict()
                if (await membership_seats(session, tenant_id)).limit is None:
                    raise InvitationIneligible()
                if invitation.generation == 2**31 - 1:
                    raise InvitationConflict()
                await self._issuance_limit(session, tenant_id, now)
                token = prepare_invitation_token()
                invitation.generation += 1
                invitation.token_digest = invitation_digest(token)
                invitation.issued_at = now
                invitation.expires_at = now + timedelta(seconds=self.settings.ttl_seconds)
                invitation.issued_by_user_id = actor_id
                invitation.issued_by_membership_id = owner.id
            else:
                invitation.state = "revoked"
                invitation.revoked_at = now
            invitation.updated_at = now
            await session.flush()
            await self._event(
                session,
                invitation,
                action=action,
                actor_id=actor_id,
                now=now,
                operation_id=request.operation_id,
                request_digest=digest,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return InvitationMutation(self._snapshot(invitation, now), False, token)

    @asynccontextmanager
    async def _transaction(self, *, accepting: bool = False) -> AsyncIterator[AsyncSession]:
        if not self.settings.enabled:
            raise InvitationUnavailable()
        try:
            async with self.session_factory.begin() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                yield session
        except AdmissionAccountConflict:
            raise InvitationAccountConflict() from None
        except AdmissionBusy:
            raise InvitationBusy() from None
        except IntegrityError:
            if accepting:
                raise InvitationAccountConflict() from None
            raise InvitationConflict() from None
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"55P03", "40P01", "40001", "57014"}:
                raise InvitationBusy() from None
            raise InvitationUnavailable() from None

    @staticmethod
    async def _now(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise InvitationUnavailable()
        return value

    @staticmethod
    async def _tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
        if tenant is None or not tenant.is_active:
            raise InvitationNotFound()
        return tenant

    @staticmethod
    async def _owner(session: AsyncSession, tenant_id: UUID, actor_id: UUID) -> Membership:
        row = (
            await session.execute(
                select(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    User.id == actor_id,
                    User.is_active.is_(True),
                    Membership.tenant_id == tenant_id,
                    Membership.role == "owner",
                    Membership.is_active.is_(True),
                )
                .with_for_update(nowait=True)
            )
        ).one_or_none()
        if row is None:
            raise InvitationForbidden()
        return cast(Membership, row[1])

    @staticmethod
    def _snapshot(invitation: MembershipInvitation, now: datetime) -> InvitationSnapshot:
        state = (
            "expired"
            if invitation.state == "pending" and now >= invitation.expires_at
            else invitation.state
        )
        return InvitationSnapshot(
            invitation_id=invitation.id,
            email=invitation.email,
            state=cast(InvitationState, state),
            generation=invitation.generation,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
            updated_at=invitation.updated_at,
        )

    async def _replay(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        operation_id: UUID,
        digest: str,
    ) -> InvitationMutation | None:
        event = await session.scalar(
            select(MembershipInvitationEvent).where(
                MembershipInvitationEvent.tenant_id == tenant_id,
                MembershipInvitationEvent.operation_id == operation_id,
            )
        )
        if event is None:
            return None
        if event.request_digest != digest:
            raise InvitationConflict()
        invitation = await self._invitation(session, tenant_id, event.invitation_id)
        return InvitationMutation(self._snapshot(invitation, await self._now(session)), True)

    @staticmethod
    async def _invitation(
        session: AsyncSession,
        tenant_id: UUID,
        invitation_id: UUID,
    ) -> MembershipInvitation:
        invitation = await session.scalar(
            select(MembershipInvitation)
            .where(
                MembershipInvitation.id == invitation_id,
                MembershipInvitation.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise InvitationNotFound()
        return invitation

    @staticmethod
    async def _issuance_limit(session: AsyncSession, tenant_id: UUID, now: datetime) -> None:
        issued = await session.scalar(
            select(func.count(MembershipInvitationEvent.id)).where(
                MembershipInvitationEvent.tenant_id == tenant_id,
                MembershipInvitationEvent.action.in_(("issued", "regenerated")),
                MembershipInvitationEvent.occurred_at > now - timedelta(hours=1),
            )
        )
        if (issued or 0) >= MAX_ISSUANCE_PER_HOUR:
            raise InvitationLimitReached()

    @staticmethod
    async def _event(
        session: AsyncSession,
        invitation: MembershipInvitation,
        *,
        action: str,
        actor_id: UUID,
        now: datetime,
        operation_id: UUID | None,
        request_digest: str | None,
        request_id: str | None,
        correlation_id: str | None,
    ) -> None:
        session.add(
            MembershipInvitationEvent(
                invitation_id=invitation.id,
                tenant_id=invitation.tenant_id,
                actor_id=actor_id,
                action=action,
                generation=invitation.generation,
                operation_id=operation_id,
                request_digest=request_digest,
                request_id=request_id,
                correlation_id=correlation_id,
                occurred_at=now,
            )
        )
        await append_audit_event(
            session,
            tenant_id=invitation.tenant_id,
            actor_id=actor_id,
            action=f"membership_invitation.{action}",
            resource_type="membership_invitation",
            resource_id=invitation.id,
            metadata={"generation": invitation.generation},
            request_id=request_id,
            correlation_id=correlation_id,
            occurred_at=now,
        )
