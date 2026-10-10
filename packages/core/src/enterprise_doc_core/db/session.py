from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from enterprise_doc_core.db.registry import register_models


def create_session_factory(
    engine: AsyncEngine | None,
) -> async_sessionmaker[AsyncSession]:
    register_models()
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def read_only_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit successful reads without permitting writes or clearing prepared SQL.

    Psycopg clears its prepared-statement cache on rollback. Keep normal rollback
    for exceptions, and let SQLAlchemy reset the connection's read-only flag when
    returning it to the pool. Write transactions retain their existing behavior.
    """
    async with factory.begin() as session:
        await session.connection(execution_options={"postgresql_readonly": True})
        yield session
