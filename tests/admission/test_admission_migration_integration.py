from __future__ import annotations

from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, MetaData, inspect, select, update
from sqlalchemy.exc import IntegrityError

from enterprise_doc_core.admission.contracts import prepare_admission_credential
from enterprise_doc_core.admission.models import (
    TenantAdmissionEvent,
    TenantAdmissionGrant,
    TenantInitialEntitlement,
)
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from tests.admission.conftest import AdmissionDatabase, migrate_admission
from tests.admission.test_admission_lifecycle_integration import (
    OPERATOR,
    grant_request,
    service_for,
)

pytestmark = pytest.mark.integration
NEW_TABLES = {"tenant_admission_grants", "tenant_admission_events", "tenant_initial_entitlements"}


def assert_metadata_matches(connection: Connection) -> None:
    expected = MetaData()
    for model in (
        Tenant,
        User,
        Membership,
        ExternalIdentityBinding,
        AuditEvent,
        TenantAdmissionGrant,
        TenantInitialEntitlement,
        TenantAdmissionEvent,
    ):
        model.__table__.to_metadata(expected)
    context = MigrationContext.configure(
        connection,
        opts={
            "include_name": lambda name, kind, parents: kind != "table" or name in expected.tables,
        },
    )
    assert compare_metadata(context, expected) == []


async def test_additive_migration_roundtrip_preserves_existing_identity(
    admission_db: AdmissionDatabase,
) -> None:
    sentinel = uuid4()
    async with admission_db.sessions.begin() as session:
        session.add(User(id=sentinel, email="migration-sentinel@example.test", is_active=True))
    async with admission_db.engine.begin() as connection:
        await connection.run_sync(assert_metadata_matches)
        await connection.run_sync(migrate_admission, "downgrade")
        tables = await connection.run_sync(
            lambda sync: inspect(sync).get_table_names(schema=admission_db.schema)
        )
        assert NEW_TABLES.isdisjoint(tables)
        assert (
            await connection.scalar(select(User.email).where(User.id == sentinel))
            == "migration-sentinel@example.test"
        )
        await connection.run_sync(migrate_admission, "upgrade")
        await connection.run_sync(assert_metadata_matches)
        assert (
            await connection.scalar(select(User.email).where(User.id == sentinel))
            == "migration-sentinel@example.test"
        )


async def test_database_rejects_partial_consumption_and_invalid_entitlements(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    for invalid in ({"state": "accepted"}, {"quota_bytes": 0}, {"seat_limit": 0}):
        with pytest.raises(IntegrityError):
            async with admission_db.sessions.begin() as session:
                await session.execute(
                    update(TenantAdmissionGrant)
                    .where(TenantAdmissionGrant.id == credential.grant_id)
                    .values(**invalid)
                )
    snapshot = await service.show(operator=OPERATOR, grant_id=credential.grant_id)
    assert snapshot.state == "pending" and snapshot.quota_bytes == 4096 and snapshot.seat_limit == 3
