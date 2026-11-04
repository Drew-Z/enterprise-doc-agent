"""Real revision-chain fixtures confined to an owned loopback schema."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
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


def migrate(connection: Connection, target: str) -> None:
    schema = connection.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str) and schema.startswith("migration_test_")
    config = Config()
    config.set_main_option(
        "script_location",
        str(
            Path(__file__).resolve().parents[1]
            / "packages/core/src/enterprise_doc_core/db/migrations"
        ),
    )
    scripts = ScriptDirectory.from_config(config)
    context = MigrationContext.configure(
        connection, opts={"target_metadata": metadata, "version_table_schema": schema}
    )
    current = context.get_current_revision()
    revision = scripts.get_revision(target)
    desired = revision.revision if revision else None
    if current == desired:
        return
    ancestors = [item.revision for item in scripts.walk_revisions(base="base", head=target)]
    upgrading = current is None or current in ancestors
    steps = (
        list(scripts.iterate_revisions(target, current or "base"))
        if upgrading
        else list(scripts.iterate_revisions(current, target))
    )
    with Operations.context(context):
        for step in reversed(steps) if upgrading else steps:
            getattr(step.module, "upgrade" if upgrading else "downgrade")()
        context.stamp(scripts, target)


@dataclass(frozen=True)
class MigrationDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def migrate(self, target: str) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(migrate, target)


@asynccontextmanager
async def migration_database(revision: str = "head") -> AsyncIterator[MigrationDatabase]:
    settings = FoundationSettings()
    url = settings.database.url.get_secret_value()
    assert settings.app_env in {"local", "test"}
    assert make_url(url).host in {"127.0.0.1", "localhost", "::1"}
    schema = "migration_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    created = False
    try:
        async with admin.begin() as connection:
            # pgvector belongs to public, never to a disposable test schema.
            assert (
                await connection.scalar(
                    text(
                        "SELECT extnamespace::regnamespace::text FROM pg_extension "
                        "WHERE extname='vector'"
                    )
                )
                == "public"
            )
            await connection.execute(CreateSchema(schema))
        created = True
        database = MigrationDatabase(engine, create_session_factory(engine))
        await database.migrate(revision)
        yield database
    finally:
        await engine.dispose()
        try:
            if created:
                async with admin.begin() as connection:
                    await connection.execute(DropSchema(schema, cascade=True))
                async with admin.connect() as connection:
                    assert not await connection.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:name)"),
                        {"name": schema},
                    )
        finally:
            await admin.dispose()
