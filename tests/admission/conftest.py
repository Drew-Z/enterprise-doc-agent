from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, MetaData, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import create_session_factory
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

ADMISSION_REVISION = "enterprise_doc_core.db.migrations.versions.20260913_0023_tenant_admission"
NEW_IDENTITY_INDEXES = {
    "ix_users_normalized_email",
    "ix_external_identity_bindings_issuer_subject_user_id",
}


def migrate_admission(connection: Connection, direction: str) -> None:
    migration = import_module(ADMISSION_REVISION)
    with Operations.context(
        MigrationContext.configure(connection, opts={"target_metadata": metadata})
    ):
        getattr(migration, direction)()


def _create_previous_dependencies(connection: Connection) -> None:
    previous = MetaData()
    for model in (Tenant, User, Membership, ExternalIdentityBinding, AuditEvent):
        table = model.__table__.to_metadata(previous)
        for index in tuple(table.indexes):
            if index.name in NEW_IDENTITY_INDEXES:
                table.indexes.remove(index)
    # Public dependencies exist too: checkfirst would find those through search_path.
    previous.create_all(connection, checkfirst=False)
    local_tables = set(
        connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        ).scalars()
    )
    assert local_tables == set(previous.tables)


@dataclass(frozen=True)
class AdmissionDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    schema: str


@pytest.fixture
async def admission_db() -> AsyncIterator[AdmissionDatabase]:
    settings = FoundationSettings()
    url = settings.database.url.get_secret_value()
    assert settings.app_env in {"local", "test"}
    assert make_url(url).host in {"127.0.0.1", "localhost", "::1"}
    schema = f"admission_test_{uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(
        url,
        connect_args={"options": f"-csearch_path={schema},public"},
        pool_size=12,
        max_overflow=0,
    )
    try:
        async with admin.begin() as connection:
            await connection.execute(CreateSchema(schema))
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == schema
            await connection.run_sync(_create_previous_dependencies)
            await connection.run_sync(migrate_admission, "upgrade")
        yield AdmissionDatabase(engine, create_session_factory(engine), schema)
    finally:
        await engine.dispose()
        try:
            async with admin.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            async with admin.connect() as connection:
                assert (
                    await connection.scalar(
                        select(text("1"))
                        .select_from(text("pg_namespace"))
                        .where(text("nspname = :schema")),
                        {"schema": schema},
                    )
                    is None
                )
        finally:
            await admin.dispose()
