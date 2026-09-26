from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.models import (
    PresalesDispatchDay,
    PresalesProviderCall,
    PresalesRouteHealth,
)
from enterprise_doc_core.presales.settings import PresalesSettings


async def available(session: AsyncSession, route_key: str, now: datetime) -> bool:
    health = await session.get(PresalesRouteHealth, route_key)
    return health is None or (
        (health.open_until is None or health.open_until <= now)
        and (health.probe_until is None or health.probe_until <= now)
    )


async def admit(
    session: AsyncSession,
    call: PresalesProviderCall,
    *,
    now: datetime,
    deadline: datetime,
    settings: PresalesSettings,
) -> None:
    # One short transaction lock covers UTC rollover, all tenants, and new route rows.
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SELECT pg_advisory_xact_lock(73482190528001)"))
    day = now.astimezone(UTC).date()
    budget = await session.get(PresalesDispatchDay, day)
    if budget is None:
        budget = PresalesDispatchDay(day=day, dispatched=0)
        session.add(budget)
    if budget.dispatched >= settings.daily_dispatch_limit:
        raise PresalesError("presales_dispatch_budget")
    health = await session.scalar(
        select(PresalesRouteHealth)
        .where(PresalesRouteHealth.route_key == call.route_key)
        .with_for_update()
    )
    if health is None:
        health = PresalesRouteHealth(route_key=call.route_key, failures=0, generation=0)
        session.add(health)
    if (health.open_until is not None and health.open_until > now) or (
        health.probe_until is not None and health.probe_until > now
    ):
        raise PresalesError("presales_route_cooling")
    if health.open_until is not None:
        health.generation += 1
        health.probe_call_id, health.probe_until = call.id, deadline
    call.health_generation = health.generation
    budget.dispatched += 1


async def observed(
    session: AsyncSession, call: PresalesProviderCall, *, now: datetime, settings: PresalesSettings
) -> None:
    health = await session.scalar(
        select(PresalesRouteHealth)
        .where(PresalesRouteHealth.route_key == call.route_key)
        .with_for_update()
    )
    if health is None or health.generation != call.health_generation:
        return
    if call.state in {"running", "unknown", "not_sent"}:
        # An interrupted half-open probe remains locked until its bounded deadline.
        return
    health.probe_call_id, health.probe_until = None, None
    if call.retryable:
        health.failures += 1
        if health.open_until is not None or health.failures >= settings.route_failure_threshold:
            health.open_until = now + timedelta(seconds=settings.route_cooldown_seconds)
            health.generation += 1
    else:
        # Invalid citations/prose are a completed transport, not a network outage.
        health.failures = 0
        if health.open_until is not None:
            health.open_until = None
            health.generation += 1
