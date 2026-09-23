import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.browser_sessions.contracts import session_digest
from enterprise_doc_core.browser_sessions.errors import BrowserContextStale, BrowserSessionInvalid
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.demo.limits import day_budget, lock_capacity
from enterprise_doc_core.demo.models import DemoWorkspace
from enterprise_doc_core.demo.settings import ATTEMPT_LIMIT, STORAGE_LIMIT, DemoError, DemoSettings
from enterprise_doc_core.identity.models import Membership, Tenant, User


@dataclass(frozen=True)
class DemoSnapshot:
    session_id: UUID
    tenant_id: UUID
    actor_id: UUID
    expires_at: datetime
    attempts_used: int

    @property
    def context_version(self) -> str:
        return f"{self.session_id.hex}.1"

    @property
    def principal(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id), actor_id=str(self.actor_id), role="owner"
        )


@dataclass(frozen=True)
class IssuedDemoSession:
    credential: SecretStr = field(repr=False)
    snapshot: DemoSnapshot


class DemoService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: DemoSettings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sessions = session_factory
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))

    async def start(self) -> IssuedDemoSession:
        if not self.settings.enabled:
            raise DemoError("demo_unavailable", 503)
        now = self.clock()
        credential = SecretStr("bss1_" + secrets.token_urlsafe(32))
        async with self.sessions.begin() as session:
            await lock_capacity(session)
            budget = await day_budget(
                session,
                now,
                workspace_limit=self.settings.daily_workspace_limit,
                attempt_limit=self.settings.daily_attempt_limit,
            )
            if budget.workspaces_created >= budget.workspace_limit:
                raise DemoError("demo_daily_limit")
            count = await session.scalar(
                select(func.count())
                .select_from(DemoWorkspace)
                .where(DemoWorkspace.cleaned_at.is_(None))
            )
            if (count or 0) >= self.settings.max_workspaces:
                raise DemoError("demo_capacity_reached")
            tenant_id, actor_id, workspace_id = uuid4(), uuid4(), uuid4()
            expiry = now + timedelta(seconds=self.settings.session_ttl_seconds)
            session.add(
                Tenant(
                    id=tenant_id,
                    name="演示企业",
                    slug="demo-" + tenant_id.hex,
                    quota_bytes=STORAGE_LIMIT,
                )
            )
            # Internal database identifier only. Never an email_verified assertion,
            # external identity binding, email destination or displayed identity.
            session.add(User(id=actor_id, email=f"guest-{actor_id.hex}@demo.invalid"))
            await session.flush()
            session.add(Membership(tenant_id=tenant_id, user_id=actor_id, role="owner"))
            session.add(
                TenantEntitlement(
                    tenant_id=tenant_id,
                    plan_code="public-demo",
                    version=1,
                    period_start=now,
                    period_end=expiry,
                    provider_request_limit=ATTEMPT_LIMIT,
                    created_at=now,
                    updated_at=now,
                )
            )
            workspace = DemoWorkspace(
                id=workspace_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                credential_digest=session_digest(credential),
                created_at=now,
                expires_at=expiry,
                attempts_used=0,
                daily_attempt_limit=self.settings.daily_attempt_limit,
                daily_workspace_limit=self.settings.daily_workspace_limit,
            )
            session.add(workspace)
            budget.workspaces_created += 1
        return IssuedDemoSession(
            credential, DemoSnapshot(workspace_id, tenant_id, actor_id, expiry, 0)
        )

    async def get(
        self, credential: SecretStr, context_version: str | None = None
    ) -> DemoSnapshot | None:
        async with self.sessions() as session:
            workspace = await session.scalar(
                select(DemoWorkspace).where(
                    DemoWorkspace.credential_digest == session_digest(credential)
                )
            )
            if workspace is None:
                return None
            if (
                not self.settings.enabled
                or workspace.expires_at <= self.clock()
                or workspace.revoked_at is not None
                or workspace.cleaned_at is not None
                or workspace.tenant_id is None
                or workspace.actor_id is None
            ):
                raise BrowserSessionInvalid()
            active = await session.scalar(
                select(Membership.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.tenant_id == workspace.tenant_id,
                    Membership.user_id == workspace.actor_id,
                    Membership.role == "owner",
                    Membership.is_active.is_(True),
                    Tenant.is_active.is_(True),
                    User.is_active.is_(True),
                )
            )
            if active is None:
                raise BrowserSessionInvalid()
            snapshot = DemoSnapshot(
                workspace.id,
                workspace.tenant_id,
                workspace.actor_id,
                workspace.expires_at,
                workspace.attempts_used,
            )
            if context_version is not None and context_version != snapshot.context_version:
                raise BrowserContextStale()
            return snapshot

    async def logout(self, credential: SecretStr, context_version: str) -> bool:
        async with self.sessions.begin() as session:
            workspace = await session.scalar(
                select(DemoWorkspace)
                .where(DemoWorkspace.credential_digest == session_digest(credential))
                .with_for_update()
            )
            if workspace is None:
                return False
            if context_version != f"{workspace.id.hex}.1":
                raise BrowserContextStale()
            if workspace.revoked_at is None:
                workspace.revoked_at = self.clock()
            return True
