from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_doc_core.db import create_session_factory
from enterprise_doc_core.identity.models import Tenant


@pytest.fixture
async def billing_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID]]
]:
    url = make_url(
        os.environ.get(
            "FOUNDATION_TEST_DATABASE_URL",
            "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
        )
    ).set(drivername="postgresql+psycopg")
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Billing integration requires a loopback PostgreSQL database")
    engine = create_async_engine(url)
    factory = create_session_factory(engine)
    tenant_ids = (uuid4(), uuid4())
    try:
        async with factory.begin() as session:
            session.add_all(
                Tenant(
                    id=value,
                    name="billing lifecycle",
                    slug=f"billing-{value.hex}",
                    quota_bytes=1024,
                )
                for value in tenant_ids
            )
        yield factory, tenant_ids
    finally:
        try:
            async with factory.begin() as session:
                await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        finally:
            await engine.dispose()
