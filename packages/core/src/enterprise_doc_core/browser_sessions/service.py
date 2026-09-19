from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.admission.contracts import (
    VerifiedAdmissionIdentity,
    exact_identity_value,
    normalized_email,
)
from enterprise_doc_core.browser_sessions.contracts import (
    RANDOM_VALUE,
    SESSION_VALUE,
    BrowserSessionSnapshot,
    BrowserTenantChoice,
    ClaimedLogin,
    IssuedBrowserSession,
    LoginStart,
    digest,
    session_digest,
)
from enterprise_doc_core.browser_sessions.errors import (
    BrowserContextStale,
    BrowserIdentityConflict,
    BrowserLoginInvalid,
    BrowserLoginRateLimited,
    BrowserPrincipalForbidden,
    BrowserSessionBusy,
    BrowserSessionInvalid,
    BrowserSessionUnavailable,
)
from enterprise_doc_core.browser_sessions.models import (
    BrowserLoginAttempt,
    BrowserSession,
    BrowserSessionEvent,
)
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User


class BrowserSessionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        trusted_issuer: str,
        login_ttl_seconds: int = 300,
        session_ttl_seconds: int = 28800,
        login_limit_per_minute: int = 30,
    ) -> None:
        self.sessions = session_factory
        self.issuer = exact_identity_value(trusted_issuer)
        if not 1 <= login_ttl_seconds <= 600 or not 1 <= session_ttl_seconds <= 86400:
            raise ValueError("invalid browser session lifetime")
        if not 1 <= login_limit_per_minute <= 300:
            raise ValueError("invalid browser login rate")
        self.login_ttl = login_ttl_seconds
        self.session_ttl = session_ttl_seconds
        self.login_limit = login_limit_per_minute

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self.sessions.begin() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                yield session
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"55P03", "40P01", "40001"}:
                raise BrowserSessionBusy() from None
            raise BrowserSessionUnavailable() from None

    async def begin_login(
        self,
        *,
        rate_key: str,
        previous_credential: SecretStr | None = None,
        previous_login_verifier: SecretStr | None = None,
    ) -> LoginStart:
        if not re.fullmatch(r"[0-9a-f]{64}", rate_key):
            raise BrowserLoginInvalid()
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        previous_digest = _previous_digest(previous_credential)
        async with self._transaction() as session:
            lock_key = int.from_bytes(bytes.fromhex(rate_key[:16]), "big", signed=True)
            await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            now = await _now(session)
            count = await session.scalar(
                select(func.count())
                .select_from(BrowserLoginAttempt)
                .where(
                    BrowserLoginAttempt.client_digest == rate_key,
                    BrowserLoginAttempt.created_at >= now - timedelta(minutes=1),
                )
            )
            if count is not None and count >= self.login_limit:
                raise BrowserLoginRateLimited()
            await self._supersede_login(session, previous_login_verifier, previous_digest)
            now = await _now(session)
            previous = (
                await session.scalar(
                    select(BrowserSession).where(
                        BrowserSession.credential_digest == previous_digest,
                    )
                )
                if previous_digest is not None
                else None
            )
            if previous is not None and (
                not _active(previous, now) or previous.issuer != self.issuer
            ):
                previous = None
            expires = now + timedelta(seconds=self.login_ttl)
            session.add(
                BrowserLoginAttempt(
                    id=uuid4(),
                    state_digest=digest(state),
                    nonce_digest=digest(nonce),
                    verifier_digest=digest(verifier),
                    client_digest=rate_key,
                    created_at=now,
                    expires_at=expires,
                    previous_session_id=previous.id if previous is not None else None,
                    previous_generation=previous.generation if previous is not None else None,
                    previous_credential_digest=previous_digest,
                )
            )
            return LoginStart(SecretStr(state), SecretStr(nonce), SecretStr(verifier), expires)

    async def claim_login(
        self,
        *,
        state: SecretStr,
        verifier: SecretStr,
        previous_credential: SecretStr | None = None,
    ) -> ClaimedLogin:
        raw_state, raw_verifier = state.get_secret_value(), verifier.get_secret_value()
        if not RANDOM_VALUE.fullmatch(raw_state) or not RANDOM_VALUE.fullmatch(raw_verifier):
            raise BrowserLoginInvalid()
        async with self._transaction() as session:
            attempt = await session.scalar(
                select(BrowserLoginAttempt)
                .where(
                    BrowserLoginAttempt.state_digest == digest(raw_state),
                )
                .with_for_update()
            )
            now = await _now(session)
            if (
                attempt is None
                or attempt.consumed_at is not None
                or attempt.superseded_at is not None
                or now >= attempt.expires_at
                or not hmac.compare_digest(attempt.verifier_digest, digest(raw_verifier))
                or attempt.previous_credential_digest != _previous_digest(previous_credential)
            ):
                raise BrowserLoginInvalid()
            attempt.consumed_at = now
            return ClaimedLogin(attempt.id, attempt.nonce_digest, attempt.created_at)

    async def complete_login(
        self,
        *,
        attempt_id: UUID,
        identity: VerifiedAdmissionIdentity,
        previous_credential: SecretStr | None = None,
    ) -> IssuedBrowserSession:
        verified = self._verified(identity)
        credential = SecretStr("bss1_" + secrets.token_urlsafe(32))
        async with self._transaction() as session:
            attempt = await session.get(BrowserLoginAttempt, attempt_id, with_for_update=True)
            now = await _now(session)
            if (
                attempt is None
                or attempt.consumed_at is None
                or attempt.completed_at is not None
                or attempt.superseded_at is not None
                or now >= attempt.expires_at
                or attempt.previous_credential_digest != _previous_digest(previous_credential)
            ):
                raise BrowserLoginInvalid()
            if attempt.previous_session_id is not None:
                row = await session.get(
                    BrowserSession, attempt.previous_session_id, with_for_update=True
                )
                now = await _now(session)
                if (
                    row is None
                    or not _active(row, now)
                    or now >= attempt.expires_at
                    or row.credential_digest != attempt.previous_credential_digest
                    or row.generation != attempt.previous_generation
                    or row.issuer != self.issuer
                ):
                    raise BrowserLoginInvalid()
                row.generation += 1
            else:
                row = BrowserSession(id=uuid4(), generation=1, created_at=now)
                session.add(row)
            row.credential_digest = session_digest(credential)
            row.issuer, row.subject, row.email = verified.issuer, verified.subject, verified.email
            row.expires_at = now + timedelta(seconds=self.session_ttl)
            row.revoked_at = None
            row.tenant_id = row.user_id = row.membership_id = row.binding_id = None
            attempt.completed_at = now
            attempt.completed_session_id = row.id
            attempt.completed_generation = row.generation
            _event(session, row, "signed_in", now)
            return IssuedBrowserSession(credential, _snapshot(row))

    async def _supersede_login(
        self, session: AsyncSession, verifier: SecretStr | None, previous_digest: str | None
    ) -> None:
        if verifier is None or not RANDOM_VALUE.fullmatch(verifier.get_secret_value()):
            return
        attempt = await session.scalar(
            select(BrowserLoginAttempt)
            .where(BrowserLoginAttempt.verifier_digest == digest(verifier.get_secret_value()))
            .with_for_update()
        )
        if attempt is None or attempt.superseded_at is not None:
            return
        attempt.superseded_at = await _now(session)
        if attempt.completed_session_id is None:
            return
        row = await session.get(BrowserSession, attempt.completed_session_id, with_for_update=True)
        now = await _now(session)
        if (
            row is not None
            and _active(row, now)
            and row.generation == attempt.completed_generation
            and row.credential_digest != previous_digest
        ):
            # The older response's cookie has not been observed by this browser.
            # Invalidate it before a newer callback can complete with another identity.
            row.revoked_at = now
            row.generation += 1
            _event(session, row, "signed_out", now)

    async def get_session(
        self, *, credential: SecretStr, context_version: str | None = None
    ) -> BrowserSessionSnapshot:
        async with self._transaction() as session:
            row = await self._load(session, credential, context_version)
            return _snapshot(row, await self._selected(session, row))

    async def list_tenants(
        self, *, credential: SecretStr, context_version: str
    ) -> tuple[BrowserTenantChoice, ...]:
        async with self._transaction() as session:
            row = await self._load(session, credential, context_version)
            return await self._choices(session, row)

    async def select_tenant(
        self, *, credential: SecretStr, context_version: str, tenant_id: UUID
    ) -> IssuedBrowserSession:
        rotated = SecretStr("bss1_" + secrets.token_urlsafe(32))
        async with self._transaction() as session:
            row = await self._load(session, credential, context_version, lock=True)
            choices = await self._choices(session, row)
            selected = next((choice for choice in choices if choice.tenant_id == tenant_id), None)
            if selected is None:
                raise BrowserPrincipalForbidden()
            now = await _now(session)
            if not _active(row, now):
                raise BrowserSessionInvalid()
            row.tenant_id, row.user_id = selected.tenant_id, selected.user_id
            row.membership_id, row.binding_id = selected.membership_id, selected.binding_id
            row.generation += 1
            row.credential_digest = session_digest(rotated)
            _event(session, row, "selected", now)
            return IssuedBrowserSession(rotated, _snapshot(row, selected))

    async def authorize(self, *, credential: SecretStr, context_version: str) -> PrincipalContext:
        snapshot = await self.get_session(credential=credential, context_version=context_version)
        selected = snapshot.selected
        if selected is None:
            raise BrowserPrincipalForbidden()
        return PrincipalContext(
            tenant_id=str(selected.tenant_id), actor_id=str(selected.user_id), role=selected.role
        )

    async def logout(self, *, credential: SecretStr, context_version: str) -> datetime:
        async with self._transaction() as session:
            row = await self._load(session, credential, context_version, lock=True)
            now = await _now(session)
            row.revoked_at = now
            row.generation += 1
            _event(session, row, "signed_out", now)
            return now

    async def _choices(
        self, session: AsyncSession, row: BrowserSession
    ) -> tuple[BrowserTenantChoice, ...]:
        statement = (
            select(ExternalIdentityBinding, Tenant, User, Membership)
            .select_from(ExternalIdentityBinding)
            .join(Tenant, Tenant.id == ExternalIdentityBinding.tenant_id)
            .join(User, User.id == ExternalIdentityBinding.user_id)
            .outerjoin(
                Membership, (Membership.tenant_id == Tenant.id) & (Membership.user_id == User.id)
            )
            .where(
                ExternalIdentityBinding.issuer == row.issuer,
                ExternalIdentityBinding.subject == row.subject,
            )
            .limit(1001)
        )
        rows = (await session.execute(statement)).all()
        if len(rows) > 1000 or len({binding.user_id for binding, _, _, _ in rows}) > 1:
            raise BrowserIdentityConflict()
        choices = [
            BrowserTenantChoice(
                tenant.id, tenant.name, user.id, membership.id, binding.id, membership.role
            )
            for binding, tenant, user, membership in rows
            if binding.is_active
            and tenant.is_active
            and user.is_active
            and membership is not None
            and membership.is_active
            and membership.role in {"owner", "member"}
        ]
        return tuple(
            sorted(choices, key=lambda choice: (choice.name.casefold(), str(choice.tenant_id)))
        )

    async def _selected(
        self, session: AsyncSession, row: BrowserSession
    ) -> BrowserTenantChoice | None:
        if row.tenant_id is None:
            return None
        return next(
            (
                choice
                for choice in await self._choices(session, row)
                if (choice.tenant_id, choice.user_id, choice.membership_id, choice.binding_id)
                == (row.tenant_id, row.user_id, row.membership_id, row.binding_id)
            ),
            None,
        )

    async def _load(
        self,
        session: AsyncSession,
        credential: SecretStr,
        context_version: str | None,
        *,
        lock: bool = False,
    ) -> BrowserSession:
        statement = select(BrowserSession).where(
            BrowserSession.credential_digest == session_digest(credential)
        )
        if lock:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None or not _active(row, await _now(session)) or row.issuer != self.issuer:
            raise BrowserSessionInvalid()
        if context_version is not None and context_version != f"{row.id.hex}.{row.generation}":
            raise BrowserContextStale()
        return row

    def _verified(self, identity: VerifiedAdmissionIdentity) -> VerifiedAdmissionIdentity:
        if (
            not isinstance(identity, VerifiedAdmissionIdentity)
            or identity.email_verified is not True
        ):
            raise BrowserLoginInvalid()
        try:
            issuer = exact_identity_value(identity.issuer)
            subject = exact_identity_value(identity.subject)
            email = normalized_email(identity.email)
        except (TypeError, ValueError, AttributeError):
            raise BrowserLoginInvalid() from None
        if issuer != self.issuer:
            raise BrowserLoginInvalid()
        return VerifiedAdmissionIdentity(issuer, subject, email, True)


async def _now(session: AsyncSession) -> datetime:
    result = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(result, datetime):
        raise BrowserSessionUnavailable()
    return result


def _active(row: BrowserSession, now: datetime) -> bool:
    return row.revoked_at is None and now < row.expires_at


def _previous_digest(credential: SecretStr | None) -> str | None:
    if credential is None or not SESSION_VALUE.fullmatch(credential.get_secret_value()):
        return None
    return session_digest(credential)


def _snapshot(
    row: BrowserSession, selected: BrowserTenantChoice | None = None
) -> BrowserSessionSnapshot:
    return BrowserSessionSnapshot(
        row.id,
        row.generation,
        VerifiedAdmissionIdentity(row.issuer, row.subject, row.email, True),
        row.expires_at,
        selected,
    )


def _event(session: AsyncSession, row: BrowserSession, action: str, now: datetime) -> None:
    session.add(
        BrowserSessionEvent(
            id=uuid4(),
            session_id=row.id,
            generation=row.generation,
            action=action,
            occurred_at=now,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
        )
    )
