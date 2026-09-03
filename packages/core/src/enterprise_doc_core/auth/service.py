from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit.service import append_audit_event
from enterprise_doc_core.auth.models import LocalTokenRevocation


@dataclass(frozen=True, slots=True)
class LocalTokenRevocationResult:
    revocation_id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    token_id: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime
    reason: str
    already_revoked: bool


class LocalTokenRevocationError(ValueError):
    pass


class LocalTokenRevocationService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def revoke(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        token_id: str,
        issued_at: datetime,
        expires_at: datetime,
        reason: str = "logout",
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> LocalTokenRevocationResult:
        _validate_inputs(
            token_id=token_id,
            issued_at=issued_at,
            expires_at=expires_at,
            reason=reason,
        )
        effective_now = datetime.now(UTC)
        async with self.session_factory.begin() as session:
            existing = await session.scalar(
                select(LocalTokenRevocation)
                .where(
                    LocalTokenRevocation.tenant_id == tenant_id,
                    LocalTokenRevocation.token_id == token_id,
                )
                .with_for_update()
            )
            if existing is not None:
                return _result(existing, already_revoked=True)

            revocation = LocalTokenRevocation(
                tenant_id=tenant_id,
                actor_id=actor_id,
                token_id=token_id,
                issued_at=issued_at,
                expires_at=expires_at,
                revoked_at=effective_now,
                reason=reason,
            )
            try:
                async with session.begin_nested():
                    session.add(revocation)
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(LocalTokenRevocation).where(
                        LocalTokenRevocation.tenant_id == tenant_id,
                        LocalTokenRevocation.token_id == token_id,
                    )
                )
                if existing is None:
                    raise
                return _result(existing, already_revoked=True)

            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="auth.session.revoked",
                resource_type="auth_session",
                resource_id=revocation.id,
                metadata={
                    "token_id": token_id,
                    "reason": reason,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return _result(revocation, already_revoked=False)

    async def is_revoked(self, *, tenant_id: UUID, token_id: str) -> bool:
        if not token_id:
            return False
        async with self.session_factory() as session:
            revocation_id = await session.scalar(
                select(LocalTokenRevocation.id).where(
                    LocalTokenRevocation.tenant_id == tenant_id,
                    LocalTokenRevocation.token_id == token_id,
                )
            )
        return revocation_id is not None

    async def purge_expired(self, *, now: datetime | None = None, limit: int = 500) -> int:
        if not 1 <= limit <= 5000:
            raise LocalTokenRevocationError("purge limit must be between 1 and 5000")
        effective_now = _normalise_datetime(now or datetime.now(UTC))
        async with self.session_factory.begin() as session:
            ids = tuple(
                (
                    await session.scalars(
                        select(LocalTokenRevocation.id)
                        .where(LocalTokenRevocation.expires_at <= effective_now)
                        .order_by(
                            LocalTokenRevocation.expires_at.asc(),
                            LocalTokenRevocation.id.asc(),
                        )
                        .limit(limit)
                    )
                ).all()
            )
            if not ids:
                return 0
            await session.execute(
                delete(LocalTokenRevocation).where(LocalTokenRevocation.id.in_(ids))
            )
            return len(ids)


def _validate_inputs(
    *,
    token_id: str,
    issued_at: datetime,
    expires_at: datetime,
    reason: str,
) -> None:
    if (
        not token_id
        or len(token_id) > 128
        or token_id != token_id.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in token_id)
    ):
        raise LocalTokenRevocationError("token id is invalid")
    if expires_at <= issued_at:
        raise LocalTokenRevocationError("token expiry must be after issuance")
    if not 1 <= len(reason.strip()) <= 80:
        raise LocalTokenRevocationError("revocation reason is invalid")


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _result(
    revocation: LocalTokenRevocation,
    *,
    already_revoked: bool,
) -> LocalTokenRevocationResult:
    return LocalTokenRevocationResult(
        revocation_id=revocation.id,
        tenant_id=revocation.tenant_id,
        actor_id=revocation.actor_id,
        token_id=revocation.token_id,
        issued_at=revocation.issued_at,
        expires_at=revocation.expires_at,
        revoked_at=revocation.revoked_at,
        reason=revocation.reason,
        already_revoked=already_revoked,
    )
