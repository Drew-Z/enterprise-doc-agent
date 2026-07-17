from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_api.auth.jwt import JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import AppEnvironment
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class BootstrapNotAllowed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant_id: UUID
    actor_id: UUID
    role: str
    token: str


def ensure_bootstrap_allowed(environment: AppEnvironment) -> None:
    if environment not in {AppEnvironment.LOCAL, AppEnvironment.TEST}:
        raise BootstrapNotAllowed("local principal bootstrap is forbidden outside local/test")


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 320 or _EMAIL_PATTERN.fullmatch(normalized) is None:
        raise ValueError("email must be a valid address")
    return normalized


def normalize_slug(value: str) -> str:
    normalized = value.strip().lower()
    if _SLUG_PATTERN.fullmatch(normalized) is None:
        raise ValueError("slug must contain 3-64 lowercase letters, digits, underscores, or dashes")
    return normalized


async def bootstrap_principal(
    *,
    settings: ApiSettings,
    session_factory: async_sessionmaker[AsyncSession],
    tenant_name: str,
    tenant_slug: str,
    email: str,
    role: MembershipRole,
    quota_bytes: int,
) -> BootstrapResult:
    ensure_bootstrap_allowed(settings.app_env)
    normalized_slug = normalize_slug(tenant_slug)
    normalized_email = normalize_email(email)
    if not tenant_name.strip() or len(tenant_name.strip()) > 200:
        raise ValueError("tenant name must contain 1-200 characters")
    if quota_bytes <= 0:
        raise ValueError("quota bytes must be positive")

    async with session_factory.begin() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == normalized_slug))
        if tenant is None:
            tenant = Tenant(
                name=tenant_name.strip(),
                slug=normalized_slug,
                quota_bytes=quota_bytes,
            )
            session.add(tenant)
        else:
            tenant.name = tenant_name.strip()
            tenant.is_active = True
            if tenant.used_storage_bytes + tenant.reserved_storage_bytes > quota_bytes:
                raise ValueError("quota cannot be lower than current storage counters")
            tenant.quota_bytes = quota_bytes

        user = await session.scalar(select(User).where(User.email == normalized_email))
        if user is None:
            user = User(email=normalized_email)
            session.add(user)
        else:
            user.is_active = True
        await session.flush()

        membership = await session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.user_id == user.id,
            )
        )
        if membership is None:
            membership = Membership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=role.value,
            )
            session.add(membership)
        else:
            membership.role = role.value
            membership.is_active = True

    token = JwtTokenCodec(settings.auth).issue_local_token(
        tenant_id=tenant.id,
        actor_id=user.id,
    )
    return BootstrapResult(
        tenant_id=tenant.id,
        actor_id=user.id,
        role=role.value,
        token=token,
    )
