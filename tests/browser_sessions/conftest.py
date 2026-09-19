from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, MetaData, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import create_session_factory
from enterprise_doc_core.db.metadata import metadata

BROWSER_TABLES = {"browser_login_attempts", "browser_sessions", "browser_session_events"}
BROWSER_REVISION = "enterprise_doc_core.db.migrations.versions.20260913_0024_browser_sessions"


def migrate_browser(connection: Connection, direction: str) -> None:
    module = import_module(BROWSER_REVISION)
    with Operations.context(
        MigrationContext.configure(connection, opts={"target_metadata": metadata})
    ):
        getattr(module, direction)()


def create_previous(connection: Connection) -> None:
    previous = MetaData(naming_convention=metadata.naming_convention)
    for table in metadata.tables.values():
        if table.name not in BROWSER_TABLES:
            table.to_metadata(previous)
    previous.create_all(connection, checkfirst=False)
    actual = set(
        connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
        )
    )
    assert actual == set(previous.tables)


@dataclass(frozen=True)
class BrowserDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    schema: str


@pytest.fixture
async def browser_db() -> AsyncIterator[BrowserDatabase]:
    settings = FoundationSettings()
    url = settings.database.url.get_secret_value()
    assert settings.app_env in {"local", "test"}
    assert make_url(url).host in {"127.0.0.1", "localhost", "::1"}
    schema = "browser_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}, pool_size=8, max_overflow=0
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(CreateSchema(schema))
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == schema
            await connection.run_sync(create_previous)
            await connection.run_sync(migrate_browser, "upgrade")
        yield BrowserDatabase(engine, create_session_factory(engine), schema)
    finally:
        await engine.dispose()
        try:
            async with admin.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            async with admin.connect() as connection:
                assert not await connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                    {"schema": schema},
                )
        finally:
            await admin.dispose()
