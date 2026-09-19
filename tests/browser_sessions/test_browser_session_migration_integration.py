from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, MetaData, text

from enterprise_doc_core.db.metadata import metadata

from .conftest import BROWSER_TABLES, BrowserDatabase, migrate_browser

pytestmark = pytest.mark.integration


def differences(connection: Connection) -> list:
    expected = MetaData(naming_convention=metadata.naming_convention)
    for name in BROWSER_TABLES:
        metadata.tables[name].to_metadata(expected)
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "include_name": lambda name, kind, _: kind != "table" or name in BROWSER_TABLES,
        },
    )
    return compare_metadata(context, expected)


async def test_real_browser_migration_matches_models_and_reverses_only_its_tables(
    browser_db: BrowserDatabase,
) -> None:
    async with browser_db.engine.begin() as connection:
        assert await connection.run_sync(differences) == []
        before = set(
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
                )
            ).scalars()
        )
        await connection.run_sync(migrate_browser, "downgrade")
        after = set(
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
                )
            ).scalars()
        )
        assert before - after == BROWSER_TABLES
        await connection.run_sync(migrate_browser, "upgrade")
        assert await connection.run_sync(differences) == []
