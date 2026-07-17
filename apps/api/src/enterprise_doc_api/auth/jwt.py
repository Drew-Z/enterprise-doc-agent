from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_api.config import AuthSettings
from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity import Membership, Tenant, User


class InvalidBearerToken(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="auth_invalid",
            message="The bearer token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class PrincipalForbidden(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="principal_forbidden",
            message="The principal does not have an active tenant membership.",
        )


@dataclass(frozen=True, slots=True)
class JwtClaims:
    tenant_id: UUID
    actor_id: UUID
    token_id: str


class JwtTokenCodec:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings

    def issue_local_token(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        now: datetime | None = None,
    ) -> str:
        issued_at = now or datetime.now(UTC)
        token_id = str(uuid4())
        payload = {
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "sub": str(actor_id),
            "tenant_id": str(tenant_id),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + timedelta(seconds=self.settings.token_ttl_seconds),
            "jti": token_id,
        }
        return str(
            jwt.encode(
                payload,
                self.settings.signing_key.get_secret_value(),
                algorithm="HS256",
            )
        )

    def decode(self, token: str) -> JwtClaims:
        if not token or len(token) > self.settings.max_token_length:
            raise InvalidBearerToken()
        try:
            payload = jwt.decode(
                token,
                self.settings.signing_key.get_secret_value(),
                algorithms=["HS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["iss", "aud", "sub", "tenant_id", "iat", "nbf", "exp", "jti"]},
            )
            actor_id = UUID(str(payload["sub"]))
            tenant_id = UUID(str(payload["tenant_id"]))
            token_id = str(payload["jti"])
            if not token_id or len(token_id) > 128:
                raise ValueError("invalid token id")
        except (KeyError, TypeError, ValueError, PyJWTError) as exc:
            raise InvalidBearerToken() from exc
        return JwtClaims(tenant_id=tenant_id, actor_id=actor_id, token_id=token_id)


class DatabasePrincipalResolver:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
        codec: JwtTokenCodec,
    ) -> None:
        self.session_factory = session_factory
        self.codec = codec

    async def resolve(self, token: str) -> PrincipalContext:
        claims = self.codec.decode(token)
        if self.session_factory is None:
            raise RuntimeError("principal resolver session factory is unavailable")

        statement = (
            select(Membership.role)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == claims.tenant_id,
                Membership.user_id == claims.actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        async with self.session_factory() as session:
            role = await session.scalar(statement)
        if role is None:
            raise PrincipalForbidden()
        return PrincipalContext(
            tenant_id=str(claims.tenant_id),
            actor_id=str(claims.actor_id),
            role=role,
        )
