from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.demo.models import DemoDay, DemoWorkspace
from enterprise_doc_core.demo.settings import (
    ATTEMPT_LIMIT,
    FILE_SIZE_LIMIT,
    PACKET_LIMIT,
    ROW_LIMIT,
    UPLOAD_LIMIT,
    DemoError,
)
from enterprise_doc_core.presales.models import PresalesPacket


async def lock_capacity(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SELECT pg_advisory_xact_lock(73482190527001)"))


async def day_budget(
    session: AsyncSession, now: datetime, *, workspace_limit: int, attempt_limit: int
) -> DemoDay:
    # Caller holds the global transaction lock, including across UTC midnight.
    day = now.astimezone(UTC).date()
    budget = await session.get(DemoDay, day)
    if budget is None:
        budget = DemoDay(
            day=day,
            workspaces_created=0,
            attempts_used=0,
            workspace_limit=workspace_limit,
            attempt_limit=attempt_limit,
        )
        session.add(budget)
        await session.flush()
    return budget


async def active_workspace(
    session: AsyncSession, tenant_id: UUID, now: datetime
) -> DemoWorkspace | None:
    workspace = await session.scalar(
        select(DemoWorkspace).where(DemoWorkspace.tenant_id == tenant_id)
    )
    if workspace is not None and (
        workspace.expires_at <= now
        or workspace.revoked_at is not None
        or workspace.cleaned_at is not None
    ):
        raise DemoError("demo_session_expired", 401)
    return workspace


async def check_upload(session: AsyncSession, tenant_id: UUID, size_bytes: int) -> None:
    from enterprise_doc_core.uploads.models import UploadSession

    # UploadCreationService already locks the tenant before this check and insert.
    if await active_workspace(session, tenant_id, datetime.now(UTC)) is None:
        return
    count = await session.scalar(
        select(func.count()).select_from(UploadSession).where(UploadSession.tenant_id == tenant_id)
    )
    if size_bytes > FILE_SIZE_LIMIT or (count or 0) >= UPLOAD_LIMIT:
        raise DemoError("demo_upload_limit")


async def check_packet(
    session: AsyncSession, tenant_id: UUID, row_count: int, now: datetime
) -> None:
    if await active_workspace(session, tenant_id, now) is None:
        return
    count = await session.scalar(
        select(func.count())
        .select_from(PresalesPacket)
        .where(PresalesPacket.tenant_id == tenant_id)
    )
    if row_count > ROW_LIMIT or (count or 0) >= PACKET_LIMIT:
        raise DemoError("demo_packet_limit")


async def reserve_attempt(
    session: AsyncSession,
    tenant_id: UUID,
    attempt_id: UUID,
    now: datetime,
    deadline: datetime,
    *,
    background: bool = False,
) -> None:
    workspace = await active_workspace(session, tenant_id, now)
    if workspace is None:
        return
    await lock_capacity(session)
    # Tenant lock serializes this workspace; the global lock serializes all demos.
    budget = await day_budget(
        session,
        now,
        workspace_limit=workspace.daily_workspace_limit,
        attempt_limit=workspace.daily_attempt_limit,
    )
    if workspace.attempts_used >= ATTEMPT_LIMIT:
        raise DemoError("demo_attempt_limit")
    if budget.attempts_used >= budget.attempt_limit:
        raise DemoError("demo_daily_limit")
    if not background:
        await _claim_execution(session, workspace, attempt_id, now, deadline)
    # These counters are never refunded on model failure, timeout or cleanup.
    budget.attempts_used += 1
    workspace.attempts_used += 1


async def _claim_execution(
    session: AsyncSession,
    workspace: DemoWorkspace,
    attempt_id: UUID,
    now: datetime,
    deadline: datetime,
) -> None:
    busy = await session.scalar(
        select(DemoWorkspace.id)
        .where(DemoWorkspace.busy_until > now, DemoWorkspace.active_attempt_id != attempt_id)
        .limit(1)
    )
    if busy is not None:
        raise DemoError("demo_generation_busy")
    workspace.active_attempt_id, workspace.busy_until = attempt_id, deadline + timedelta(seconds=30)


async def begin_background_attempt(
    session: AsyncSession, tenant_id: UUID, attempt_id: UUID, now: datetime, deadline: datetime
) -> None:
    workspace = await active_workspace(session, tenant_id, now)
    if workspace is not None:
        await lock_capacity(session)
        await _claim_execution(session, workspace, attempt_id, now, deadline)


async def finish_attempt(session: AsyncSession, tenant_id: UUID, attempt_id: UUID) -> None:
    workspace = await session.scalar(
        select(DemoWorkspace).where(DemoWorkspace.tenant_id == tenant_id).with_for_update()
    )
    if workspace is not None and workspace.active_attempt_id == attempt_id:
        workspace.active_attempt_id = None
        workspace.busy_until = None
