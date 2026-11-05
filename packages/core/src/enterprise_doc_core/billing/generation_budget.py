"""Shared generation cap across the legacy daily receipt and provider dispatch ledger.

The existing presales counter remains authoritative for its historical calls.
This narrow compatibility query avoids copying or resetting that history when a
new product starts using the shared provider ledger. Both writers take this lock
after business locks and before provider-day/operation locks.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.billing.errors import UsageError


async def require_generation_budget(
    session: AsyncSession, *, now: datetime, limit: int = 200
) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(73482190528001)"))
    # The same timestamp is persisted on the dispatch/counter by the caller.
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    count = await session.scalar(
        text(
            "SELECT (SELECT COALESCE(SUM(dispatched), 0) FROM presales_dispatch_days "
            "WHERE day = :day) + (SELECT COUNT(*) FROM provider_dispatches "
            "WHERE kind = 'agent' AND started_at >= :start AND started_at < :end)"
        ),
        {"day": start.date(), "start": start, "end": start + timedelta(days=1)},
    )
    if int(count or 0) >= min(200, limit):
        raise UsageError("provider_generation_budget_exhausted")
