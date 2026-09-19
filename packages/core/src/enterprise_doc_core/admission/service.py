from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.admission.accounts import replay_admission, resolve_admission_user
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    AdmissionReceipt,
    AdmissionSnapshot,
    PlatformAdmissionOperator,
    PreparedAdmissionCredential,
    VerifiedAdmissionIdentity,
    credential_digest,
    exact_identity_value,
    normalized_email,
    normalized_tenant_name,
    require_operator,
    value_digest,
)
from enterprise_doc_core.admission.errors import (
    AdmissionAccountConflict,
    AdmissionBusy,
    AdmissionConflict,
    AdmissionDenied,
    AdmissionError,
    AdmissionInvalid,
    AdmissionNotFound,
)
from enterprise_doc_core.admission.models import (
    TenantAdmissionEvent,
    TenantAdmissionGrant,
    TenantInitialEntitlement,
)
from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant


class TenantAdmissionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        trusted_issuers: frozenset[str],
    ) -> None:
        for issuer in trusted_issuers:
            exact_identity_value(issuer)
        self.session_factory = session_factory
        self.trusted_issuers = trusted_issuers

    @asynccontextmanager
    async def _transaction(self, *, accepting: bool = False) -> AsyncIterator[AsyncSession]:
        try:
            async with self.session_factory.begin() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                yield session
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"55P03", "40P01", "40001"}:
                raise AdmissionBusy() from None
            if isinstance(error, IntegrityError):
                if accepting:
                    raise AdmissionAccountConflict() from None
                raise AdmissionConflict() from None
            raise AdmissionError() from None

    async def issue(
        self,
        *,
        operator: PlatformAdmissionOperator,
        request: AdmissionGrantRequest,
        credential: PreparedAdmissionCredential,
    ) -> AdmissionSnapshot:
        actor = require_operator(operator)
        request = AdmissionGrantRequest.model_validate(request.model_dump())
        if request.issuer not in self.trusted_issuers:
            raise AdmissionInvalid()
        digest = credential_digest(credential.token)
        async with self._transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"admission:issue:{credential.grant_id}"},
            )
            existing = await session.get(TenantAdmissionGrant, credential.grant_id)
            now = await _database_now(session)
            if existing is not None:
                if existing.token_digest != digest or any(
                    getattr(existing, name) != value for name, value in request.model_dump().items()
                ):
                    raise AdmissionConflict()
                return _snapshot(existing, now)
            if not now < request.expires_at <= now + timedelta(days=30):
                raise AdmissionInvalid()
            grant = TenantAdmissionGrant(
                id=credential.grant_id,
                token_digest=digest,
                issued_at=now,
                state="pending",
                **request.model_dump(),
            )
            session.add(grant)
            await session.flush()
            session.add(
                TenantAdmissionEvent(
                    grant_id=grant.id,
                    action="issued",
                    occurred_at=now,
                    operator_id=actor.operator_id,
                    reason=actor.reason,
                )
            )
            return _snapshot(grant, now)

    async def show(
        self, *, operator: PlatformAdmissionOperator, grant_id: UUID
    ) -> AdmissionSnapshot:
        require_operator(operator)
        async with self._transaction() as session:
            grant = await session.get(TenantAdmissionGrant, grant_id)
            if grant is None:
                raise AdmissionNotFound()
            return _snapshot(grant, await _database_now(session))

    async def revoke(
        self, *, operator: PlatformAdmissionOperator, grant_id: UUID
    ) -> AdmissionSnapshot:
        actor = require_operator(operator)
        async with self._transaction() as session:
            grant = await session.scalar(
                select(TenantAdmissionGrant)
                .where(TenantAdmissionGrant.id == grant_id)
                .with_for_update()
            )
            if grant is None:
                raise AdmissionNotFound()
            if grant.state == "accepted":
                raise AdmissionConflict()
            now = await _database_now(session)
            if grant.state != "revoked":
                grant.state = "revoked"
                grant.revoked_at = now
                session.add(
                    TenantAdmissionEvent(
                        grant_id=grant.id,
                        action="revoked",
                        occurred_at=now,
                        operator_id=actor.operator_id,
                        reason=actor.reason,
                    )
                )
            return _snapshot(grant, now)

    async def accept(
        self,
        *,
        token: SecretStr,
        identity: VerifiedAdmissionIdentity,
        tenant_name: str,
    ) -> AdmissionReceipt:
        if (
            not isinstance(identity, VerifiedAdmissionIdentity)
            or identity.email_verified is not True
        ):
            raise AdmissionDenied()
        try:
            issuer = exact_identity_value(identity.issuer)
            subject = exact_identity_value(identity.subject)
            email = normalized_email(identity.email)
        except (ValueError, TypeError, AttributeError):
            raise AdmissionDenied() from None
        if issuer not in self.trusted_issuers:
            raise AdmissionDenied()
        try:
            name = normalized_tenant_name(tenant_name)
        except (ValueError, TypeError, AttributeError):
            raise AdmissionInvalid() from None
        digest = credential_digest(token)
        identity_hash = value_digest(issuer, subject, email)
        request_hash = value_digest(name)
        async with self._transaction(accepting=True) as session:
            grant = await session.scalar(
                select(TenantAdmissionGrant)
                .where(TenantAdmissionGrant.token_digest == digest)
                .with_for_update()
            )
            if grant is None or grant.issuer != issuer or grant.recipient_email != email:
                raise AdmissionDenied()
            now = await _database_now(session)
            if grant.state == "accepted":
                return await replay_admission(
                    session,
                    grant=grant,
                    identity_digest=identity_hash,
                    request_digest=request_hash,
                    issuer=issuer,
                    subject=subject,
                    email=email,
                )
            if grant.state != "pending" or now >= grant.expires_at:
                raise AdmissionDenied()
            keys = sorted(
                (
                    f"admission:email:{value_digest(email)}",
                    f"admission:identity:{value_digest(issuer, subject)}",
                )
            )
            for key in keys:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
                )
            user = await resolve_admission_user(
                session, issuer=issuer, subject=subject, email=email
            )
            now = await _database_now(session)
            if now >= grant.expires_at:
                raise AdmissionDenied()
            tenant = Tenant(
                id=uuid4(),
                name=name,
                is_active=True,
                quota_bytes=grant.quota_bytes,
                used_storage_bytes=0,
                reserved_storage_bytes=0,
            )
            tenant.slug = f"tenant-{tenant.id.hex}"
            session.add(tenant)
            await session.flush()
            membership = Membership(
                id=uuid4(), tenant_id=tenant.id, user_id=user.id, role="owner", is_active=True
            )
            binding = ExternalIdentityBinding(
                id=uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                issuer=issuer,
                subject=subject,
                is_active=True,
            )
            entitlement = TenantInitialEntitlement(
                id=uuid4(),
                tenant_id=tenant.id,
                grant_id=grant.id,
                quota_bytes=grant.quota_bytes,
                seat_limit=grant.seat_limit,
                created_at=now,
            )
            session.add_all((membership, binding, entitlement))
            grant.state = "accepted"
            grant.accepted_at = now
            grant.accepted_tenant_id = tenant.id
            grant.accepted_user_id = user.id
            grant.accepted_membership_id = membership.id
            grant.accepted_binding_id = binding.id
            grant.accepted_entitlement_id = entitlement.id
            grant.accepted_identity_digest = identity_hash
            grant.accepted_request_digest = request_hash
            session.add(
                TenantAdmissionEvent(
                    grant_id=grant.id, action="accepted", occurred_at=now, accepted_user_id=user.id
                )
            )
            await session.flush()
            await append_audit_event(
                session,
                tenant_id=tenant.id,
                actor_id=user.id,
                action="tenant.admission.accepted",
                resource_type="tenant_admission_grant",
                resource_id=grant.id,
                occurred_at=now,
                metadata={"grant_id": str(grant.id), "entitlement_id": str(entitlement.id)},
            )
            return AdmissionReceipt(
                grant_id=grant.id,
                tenant_id=tenant.id,
                user_id=user.id,
                membership_id=membership.id,
                binding_id=binding.id,
                entitlement_id=entitlement.id,
                tenant_slug=tenant.slug,
                accepted_at=now,
                replayed=False,
            )


async def _database_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise AdmissionError()
    return now


def _snapshot(grant: TenantAdmissionGrant, now: datetime) -> AdmissionSnapshot:
    state: Literal["pending", "expired", "accepted", "revoked"]
    if grant.state == "pending":
        state = "expired" if now >= grant.expires_at else "pending"
    elif grant.state == "accepted":
        state = "accepted"
    elif grant.state == "revoked":
        state = "revoked"
    else:
        raise AdmissionConflict()
    return AdmissionSnapshot(
        grant_id=grant.id,
        state=state,
        recipient_email=grant.recipient_email,
        issuer=grant.issuer,
        expires_at=grant.expires_at,
        quota_bytes=grant.quota_bytes,
        seat_limit=grant.seat_limit,
        issued_at=grant.issued_at,
        accepted_at=grant.accepted_at,
        revoked_at=grant.revoked_at,
        tenant_id=grant.accepted_tenant_id,
    )
