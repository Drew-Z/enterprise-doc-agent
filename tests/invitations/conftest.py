from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
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

DEPENDENCIES = {
    "tenants",
    "users",
    "memberships",
    "external_identity_bindings",
    "audit_events",
    "tenant_admission_grants",
    "tenant_admission_events",
    "tenant_initial_entitlements",
    "browser_login_attempts",
    "browser_sessions",
    "browser_session_events",
    "local_token_revocations",
}
INVITATION_TABLES = {"membership_invitations", "membership_invitation_events"}


def migrate_invitations(connection: Connection, direction: str) -> None:
    module = import_module(
        "enterprise_doc_core.db.migrations.versions.20260914_0025_membership_invitations"
    )
    with Operations.context(
        MigrationContext.configure(connection, opts={"target_metadata": metadata})
    ):
        getattr(module, direction)()


@dataclass(frozen=True)
class InvitationDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    schema: str


@pytest.fixture
async def invitation_db() -> AsyncIterator[InvitationDatabase]:
    settings = FoundationSettings()
    url = settings.database.url.get_secret_value()
    assert settings.app_env in {"local", "test"}
    assert make_url(url).host in {"127.0.0.1", "localhost", "::1"}
    schema = f"invitation_test_{uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(
        url,
        connect_args={"options": f"-csearch_path={schema},public", "application_name": schema},
        pool_size=12,
        max_overflow=0,
    )
    tables = [table for table in metadata.tables.values() if table.name in DEPENDENCIES]
    try:
        async with admin.begin() as connection:
            await connection.execute(CreateSchema(schema))
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == schema
            await connection.run_sync(
                lambda sync: metadata.create_all(sync, tables=tables, checkfirst=False)
            )
            await connection.run_sync(migrate_invitations, "upgrade")
            names = set(
                (
                    await connection.scalars(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                    )
                ).all()
            )
            assert names == DEPENDENCIES | INVITATION_TABLES
        yield InvitationDatabase(engine, create_session_factory(engine), schema)
    finally:
        await engine.dispose()
        try:
            async with admin.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            async with admin.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"),
                        {"schema": schema},
                    )
                    == 0
                )
        finally:
            await admin.dispose()
