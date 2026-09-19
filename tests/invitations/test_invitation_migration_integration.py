from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, MetaData, select, text

from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity.models import Tenant

from .conftest import DEPENDENCIES, INVITATION_TABLES, InvitationDatabase, migrate_invitations
from .support import tenant_owner

pytestmark = pytest.mark.integration


def differences(connection: Connection) -> list:
    expected = MetaData(naming_convention=metadata.naming_convention)
    for name in INVITATION_TABLES:
        metadata.tables[name].to_metadata(expected)
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_name": lambda name, kind, _: kind != "table" or name in INVITATION_TABLES,
        },
    )
    return compare_metadata(context, expected)


async def test_invitation_migration_roundtrip_matches_models_and_preserves_existing_tables(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    async with invitation_db.engine.begin() as connection:
        assert await connection.run_sync(differences) == []
        await connection.run_sync(migrate_invitations, "downgrade")
        actual = set(
            await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
            )
        )
        assert actual == DEPENDENCIES
        assert (
            await connection.scalar(select(Tenant.name).where(Tenant.id == owner.tenant_id))
            == "邀请测试企业"
        )
        await connection.run_sync(migrate_invitations, "upgrade")
        assert await connection.run_sync(differences) == []
        actual = set(
            await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
            )
        )
        assert actual == DEPENDENCIES | INVITATION_TABLES
