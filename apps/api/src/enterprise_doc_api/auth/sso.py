from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import UUID

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_api.config import AuthSettings
from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity import ExternalIdentityBinding, Membership, Tenant, User


class ExternalIdentityInvalid(ApiError):
    def __init__(self, message: str = "The external identity is invalid or incomplete.") -> None:
        super().__init__(
            status_code=401,
            code="external_identity_invalid",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ExternalPrincipalMappingError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="external_principal_forbidden",
            message="The external identity is not mapped to an active application role.",
        )


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """Normalized output of an OIDC, SAML, or gateway-specific decoder.

    The adapter owns signature and protocol validation. The resolver validates
    the normalized trust boundary and maps groups to the existing application
    principal model, so downstream authorization stays provider-agnostic.
    """

    issuer: str
    audience: str
    subject: str
    actor_id: str
    tenant_id: str
    groups: tuple[str, ...] = ()
    role: str | None = None


class ExternalIdentityAdapter(Protocol):
    async def decode(self, token: str) -> ExternalIdentity: ...


class JwksFetcher(Protocol):
    async def fetch(self, url: str) -> Mapping[str, Any]: ...


class UrllibJwksFetcher:
    """Small stdlib JWKS fetcher; deployments can inject an HTTP client instead."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str) -> Mapping[str, Any]:
        def read() -> Mapping[str, Any]:
            request = UrlRequest(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("JWKS payload must be an object")
            return cast(Mapping[str, Any], payload)

        return await asyncio.to_thread(read)


class JwksExternalIdentityAdapter:
    """Verify an OIDC JWT and normalize claims for ExternalPrincipalResolver."""

    def __init__(
        self,
        *,
        settings: AuthSettings,
        fetcher: JwksFetcher | None = None,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        if not settings.external_jwks_url:
            raise ValueError("external JWKS URL is required for the OIDC adapter")
        if cache_ttl_seconds <= 0:
            raise ValueError("JWKS cache TTL must be positive")
        self.settings = settings
        self.fetcher = fetcher or UrllibJwksFetcher()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._jwks: tuple[Mapping[str, Any], ...] = ()
        self._cached_at: float | None = None
        self._lock = asyncio.Lock()

    async def decode(self, token: str) -> ExternalIdentity:
        if not token or len(token) > self.settings.max_token_length:
            raise ExternalIdentityInvalid()
        try:
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg", ""))
            kid = header.get("kid")
            if (
                algorithm not in self.settings.external_algorithms
                or not isinstance(kid, str)
                or not kid
            ):
                raise ValueError("unapproved JWT header")
            key_data = await self._key_for(kid=kid, algorithm=algorithm)
            key = jwt.PyJWK(key_data).key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.settings.external_algorithms),
                audience=self.settings.external_audience,
                issuer=self.settings.external_issuer,
                options={"require": ["iss", "aud", "sub", "iat", "exp"]},
            )
            subject = _claim_string(claims, "sub")
            tenant_id = _claim_string(claims, self.settings.external_tenant_claim)
            actor_value = claims.get(self.settings.external_actor_claim)
            if actor_value is None:
                actor_id = subject
            elif isinstance(actor_value, str) and actor_value.strip():
                actor_id = actor_value.strip()
            else:
                raise ValueError("actor claim is invalid")
            groups = _claim_groups(claims.get(self.settings.external_groups_claim))
            role_value = claims.get(self.settings.external_role_claim)
            role = role_value if isinstance(role_value, str) else None
            return ExternalIdentity(
                issuer=_claim_string(claims, "iss"),
                audience=_claim_audience(claims.get("aud")),
                subject=subject,
                actor_id=actor_id,
                tenant_id=tenant_id,
                groups=groups,
                role=role,
            )
        except Exception as error:
            raise ExternalIdentityInvalid() from error

    async def _key_for(self, *, kid: str, algorithm: str) -> dict[str, Any]:
        await self._refresh_jwks(force=False)
        key = self._find_key(kid=kid, algorithm=algorithm)
        if key is not None:
            return key
        # A fresh cache can still miss during an IdP key rotation. Refresh once
        # so rotation is bounded by the provider response, not cache TTL.
        await self._refresh_jwks(force=True)
        key = self._find_key(kid=kid, algorithm=algorithm)
        if key is not None:
            return key
        raise ValueError("JWKS key not found")

    async def _refresh_jwks(self, *, force: bool) -> None:
        now = time.monotonic()
        if (
            not force
            and self._cached_at is not None
            and now - self._cached_at < self.cache_ttl_seconds
        ):
            return
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._cached_at is not None
                and now - self._cached_at < self.cache_ttl_seconds
            ):
                return
            payload = await self.fetcher.fetch(cast(str, self.settings.external_jwks_url))
            raw_keys = payload.get("keys")
            if not isinstance(raw_keys, list):
                raise ValueError("JWKS keys are missing")
            self._jwks = tuple(
                cast(dict[str, Any], item) for item in raw_keys if isinstance(item, Mapping)
            )
            self._cached_at = time.monotonic()

    def _find_key(self, *, kid: str, algorithm: str) -> dict[str, Any] | None:
        for key in self._jwks:
            if key.get("kid") == kid and key.get("alg", algorithm) == algorithm:
                return dict(key)
        return None


def _claim_string(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"claim {name} is missing")
    return value.strip()


def _claim_audience(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    raise ValueError("audience claim is invalid")


def _claim_groups(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError("groups claim is invalid")


class ExternalRoleMapper(Protocol):
    def map_role(self, identity: ExternalIdentity) -> str | None: ...


class ExternalMembershipResolver(Protocol):
    async def resolve_role(self, *, actor_id: UUID, tenant_id: UUID) -> str | None: ...


class ExternalIdentityBindingResolver(Protocol):
    async def resolve_actor_id(
        self, *, tenant_id: UUID, issuer: str, subject: str
    ) -> UUID | None: ...


class DatabaseExternalMembershipResolver:
    """Resolve external identities against the same active membership source as local JWT."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
    ) -> None:
        self.session_factory = session_factory

    async def resolve_role(self, *, actor_id: UUID, tenant_id: UUID) -> str | None:
        if self.session_factory is None:
            raise RuntimeError("external membership resolver session factory is unavailable")
        statement = (
            select(Membership.role)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        async with self.session_factory() as session:
            role = await session.scalar(statement)
        return str(role) if role is not None else None

    async def resolve_actor_id(self, *, tenant_id: UUID, issuer: str, subject: str) -> UUID | None:
        if self.session_factory is None:
            raise RuntimeError("external identity resolver session factory is unavailable")
        statement = (
            select(ExternalIdentityBinding.user_id)
            .join(User, User.id == ExternalIdentityBinding.user_id)
            .where(
                ExternalIdentityBinding.tenant_id == tenant_id,
                ExternalIdentityBinding.issuer == issuer,
                ExternalIdentityBinding.subject == subject,
                ExternalIdentityBinding.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        async with self.session_factory() as session:
            return cast(UUID | None, await session.scalar(statement))


class GroupRoleMapper:
    """Small default mapper suitable for an injected IdP adapter.

    Group names are intentionally explicit. Production deployments can inject
    a mapper for tenant-specific claims or ABAC policy without changing auth.
    """

    def __init__(
        self,
        *,
        owner_groups: frozenset[str] = frozenset({"owner", "tenant-owner"}),
        member_groups: frozenset[str] = frozenset({"member", "tenant-member"}),
        role_claim_enabled: bool = False,
    ) -> None:
        if owner_groups & member_groups:
            raise ValueError("external owner and member groups must not overlap")
        self.owner_groups = owner_groups
        self.member_groups = member_groups
        self.role_claim_enabled = role_claim_enabled

    def map_role(self, identity: ExternalIdentity) -> str | None:
        groups = frozenset(identity.groups)
        maps_to_owner = bool(groups & self.owner_groups)
        maps_to_member = bool(groups & self.member_groups)
        if maps_to_owner and maps_to_member:
            return None
        group_role = "owner" if maps_to_owner else "member" if maps_to_member else None

        claim_role: str | None = None
        if self.role_claim_enabled and identity.role is not None:
            if identity.role not in {"owner", "member"}:
                return None
            claim_role = identity.role
        if claim_role is not None and group_role is not None and claim_role != group_role:
            return None
        return claim_role or group_role


class ExternalPrincipalResolver:
    """Resolve a normalized external identity into the app principal contract."""

    def __init__(
        self,
        *,
        adapter: ExternalIdentityAdapter,
        settings: AuthSettings,
        membership_resolver: ExternalMembershipResolver,
        identity_binding_resolver: ExternalIdentityBindingResolver | None = None,
        role_mapper: ExternalRoleMapper | None = None,
    ) -> None:
        if not settings.external_auth_enabled:
            raise ValueError("external principal resolver requires external_auth_enabled")
        self.adapter = adapter
        self.settings = settings
        self.membership_resolver = membership_resolver
        self.identity_binding_resolver = identity_binding_resolver
        self.role_mapper = role_mapper or GroupRoleMapper(
            owner_groups=frozenset(settings.external_owner_groups),
            member_groups=frozenset(settings.external_member_groups),
            role_claim_enabled=settings.external_role_claim_enabled,
        )

    async def resolve(self, token: str) -> PrincipalContext:
        if not token or len(token) > self.settings.max_token_length:
            raise ExternalIdentityInvalid()
        identity = await self.adapter.decode(token)
        if not identity.issuer or not identity.audience or not identity.subject:
            raise ExternalIdentityInvalid()
        if self.settings.external_issuer and identity.issuer != self.settings.external_issuer:
            raise ExternalIdentityInvalid()
        if self.settings.external_audience and identity.audience != self.settings.external_audience:
            raise ExternalIdentityInvalid()
        if not identity.actor_id or not identity.tenant_id:
            raise ExternalIdentityInvalid()
        try:
            tenant_id = UUID(identity.tenant_id)
        except (TypeError, ValueError) as error:
            raise ExternalIdentityInvalid() from error
        if self.identity_binding_resolver is not None:
            bound_actor_id = await self.identity_binding_resolver.resolve_actor_id(
                tenant_id=tenant_id,
                issuer=identity.issuer,
                subject=identity.subject,
            )
            if bound_actor_id is None:
                raise ExternalPrincipalMappingError()
            resolved_actor_id = bound_actor_id
        else:
            try:
                resolved_actor_id = UUID(identity.actor_id)
            except (TypeError, ValueError):
                raise ExternalPrincipalMappingError() from None
        role = self.role_mapper.map_role(identity)
        if role not in {"owner", "member"}:
            raise ExternalPrincipalMappingError()
        active_role = await self.membership_resolver.resolve_role(
            actor_id=resolved_actor_id,
            tenant_id=tenant_id,
        )
        if active_role != role:
            raise ExternalPrincipalMappingError()
        return PrincipalContext(
            tenant_id=str(tenant_id),
            actor_id=str(resolved_actor_id),
            role=role,
        )
