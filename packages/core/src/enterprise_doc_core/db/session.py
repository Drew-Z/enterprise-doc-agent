from __future__ import annotations

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
