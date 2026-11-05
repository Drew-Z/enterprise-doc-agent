from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from scripts.maintenance_guard import GuardError
from scripts.presales_schema_expand import WORKBOOK_MIGRATION_BODY
from sqlalchemy import text
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.deployment.test_presales_schema_expand_integration import psql_session
from tests.presales.test_presales_workflow_integration import workspace as workspace
from tests.presales.test_workbook_integration import import_request

from enterprise_doc_core.db.metadata import metadata

pytestmark = pytest.mark.integration


@pytest.fixture
async def workbook_schema_database(browser_db):
    def downgrade(connection):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            import_module(
                "enterprise_doc_core.db.migrations.versions.20261009_0035_presales_workbook"
            ).downgrade()

    async with browser_db.engine.begin() as connection:
        await connection.run_sync(downgrade)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        await connection.execute(text("INSERT INTO alembic_version VALUES ('20261008_0034')"))
    return browser_db


async def test_fixed_workbook_expansion_and_replay_guard(workbook_schema_database):
    with psql_session(workbook_schema_database, workbook=True) as session:
        assert session.revision(5) == "20261008_0034"
        session.expand(30)
        assert session.revision(5) == "20261009_0035"
        assert session.query("SELECT count(*) FROM presales_packets;", 5) == "0"
        with pytest.raises(GuardError, match="preconditions"):
            session.expand(30)
    with psql_session(workbook_schema_database, workbook=True) as session:
        assert session.revision(5) == "20261009_0035"


async def test_workbook_failure_at_revision_update_rolls_back_columns(workbook_schema_database):
    async with workbook_schema_database.engine.begin() as connection:
        await connection.execute(
            text(
                """CREATE FUNCTION refuse_workbook_revision() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'interruption'; END $$"""
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER refuse_workbook_revision BEFORE UPDATE ON alembic_version "
                "FOR EACH ROW EXECUTE FUNCTION refuse_workbook_revision()"
            )
        )
    with psql_session(workbook_schema_database, workbook=True) as session:
        with pytest.raises(GuardError, match="acknowledgement"):
            session.expand(30)
    with psql_session(workbook_schema_database, workbook=True) as session:
        assert session.revision(5) == "20261008_0034"


@pytest.mark.parametrize("committed", [False, True])
async def test_lost_workbook_migration_receipt_is_observed_without_replay(
    workbook_schema_database, committed
):
    with psql_session(workbook_schema_database, workbook=True) as session:
        sql = (
            "BEGIN;\n"
            + WORKBOOK_MIGRATION_BODY
            + ("\nCOMMIT;" if committed else "")
            + "\nSELECT pg_sleep(0.5);"
        )
        with pytest.raises(GuardError, match="acknowledgement"):
            session.query(sql, 0.1)
    with psql_session(workbook_schema_database, workbook=True) as recovered:
        assert recovered.revision(5) == ("20261009_0035" if committed else "20261008_0034")


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE presales_packets DROP CONSTRAINT ck_presales_packets_presales_workbook_valid;",
        "ALTER TABLE presales_packets ALTER COLUMN workbook_content SET DEFAULT 'x'::bytea;",
        "ALTER TABLE presales_packets ALTER COLUMN workbook_metadata SET NOT NULL;",
        "ALTER TABLE presales_packets DROP CONSTRAINT ck_presales_packets_presales_workbook_valid; "
        "ALTER TABLE presales_packets ADD CONSTRAINT ck_presales_packets_presales_workbook_valid "
        "CHECK (workbook_content IS NULL);",
    ],
)
async def test_workbook_schema_shape_drift_rejects(workbook_schema_database, sql):
    with psql_session(workbook_schema_database, workbook=True) as session:
        session.expand(30)
        session.query(sql, 5)
        with pytest.raises(GuardError, match="schema"):
            session.revision(5)


async def test_release_guard_preserves_real_imported_workbook(workbook_schema_database, workspace):
    from scripts.release_switch import ReleaseCluster, ReleasePlan
    from tests.deployment.test_release_switch import Boundary
    from tests.deployment.test_workbook_release_switch import workbook_switch_data

    with psql_session(workbook_schema_database, workbook=True) as session:
        session.expand(30)
    service, _, context, _, gateway, payload = workspace
    packet = await service.import_workbook(
        context.principal, import_request(payload, 13), "release-history"
    )
    before = await service.export_workbook(context.principal, packet.id, "draft")
    with psql_session(workbook_schema_database, workbook=True) as database:
        data = workbook_switch_data()
        boundary = Boundary(data)
        cluster = ReleaseCluster(
            ReleasePlan(data),
            run=boundary,
            revision=database.revision,
            idle=database.idle,
            workbook_empty=lambda timeout: (
                database.query(
                    "SELECT NOT EXISTS (SELECT 1 FROM presales_packets "
                    "WHERE workbook_metadata IS NOT NULL OR workbook_content IS NOT NULL);",
                    timeout,
                )
                == "t"
            ),
            clock=lambda: 1,
        )
        with pytest.raises(GuardError, match="workbook history"):
            cluster.restore(100)
        assert not boundary.writes
        assert database.revision(5) == "20261009_0035"
    assert (await service.get(context.principal, packet.id)).workbook == packet.workbook
    assert await service.export_workbook(context.principal, packet.id, "draft") == before
    assert not gateway.calls
