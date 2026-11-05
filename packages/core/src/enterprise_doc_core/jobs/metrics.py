"""Read-only, index-bounded observation of due durable jobs across job types."""

from __future__ import annotations

import math

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.jobs.models import Job, JobStatus


async def read_queue_oldest_age(sessions: async_sessionmaker[AsyncSession]) -> float:
    # One index-leading status at a time avoids sorting/scanning the whole backlog.
    oldest = [
        select(Job.available_at)
        .where(Job.status == status.value, Job.available_at <= func.now())
        .order_by(Job.available_at)
        .limit(1)
        .scalar_subquery()
        for status in (JobStatus.PENDING, JobStatus.RETRY_WAIT)
    ]
    statement = select(func.extract("epoch", func.now() - func.least(*oldest, func.now())))
    async with sessions.begin() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        value = await session.scalar(statement)
    if value is None:
        raise ValueError("queue_observation_missing")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("queue_observation_invalid")
    return seconds
