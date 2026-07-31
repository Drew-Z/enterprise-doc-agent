import asyncio
import sys
from typing import Any, Protocol, cast

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from enterprise_doc_core.config import DatabaseSettings


class DatabasePoolMetrics(Protocol):
    def set_database_pool_utilization(self, utilization_percent: float) -> None: ...


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def ensure_asyncio_compatibility() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_database_engine(
    settings: DatabaseSettings,
    *,
    metrics: DatabasePoolMetrics | None = None,
) -> AsyncEngine:
    engine = create_async_engine(
        settings.url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_recycle=settings.pool_recycle_seconds,
        connect_args={"connect_timeout": settings.connect_timeout_seconds},
    )
    if metrics is not None:
        pool = cast(Any, engine.sync_engine.pool)
        capacity = settings.pool_size + settings.max_overflow

        def update(checked_out: int) -> None:
            metrics.set_database_pool_utilization(100.0 * max(checked_out, 0) / capacity)

        def on_checkout(*_: object) -> None:
            update(int(pool.checkedout()))

        def on_checkin(*_: object) -> None:
            update(int(pool.checkedout()) - 1)

        event.listen(engine.sync_engine, "checkout", on_checkout)
        event.listen(engine.sync_engine, "checkin", on_checkin)
        update(0)
    return engine
