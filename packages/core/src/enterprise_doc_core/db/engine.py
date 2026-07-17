import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from enterprise_doc_core.config import DatabaseSettings


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def ensure_asyncio_compatibility() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        settings.url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.connect_timeout_seconds},
    )
