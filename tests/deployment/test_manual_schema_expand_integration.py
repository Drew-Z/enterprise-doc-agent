from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from scripts.maintenance_guard import GuardError
from scripts.presales_schema_expand import MANUAL_MIGRATION_BODY
from sqlalchemy import text
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.deployment.test_presales_schema_expand_integration import psql_session
from tests.presales.test_presales_workflow_integration import workspace as workspace
from tests.presales.test_workbook_integration import import_request

from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.presales.schemas import ManualResponseInput, ReviewInput

pytestmark = pytest.mark.integration


@pytest.fixture
async def manual_schema_database(browser_db):
    def downgrade(connection):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            import_module(
                "enterprise_doc_core.db.migrations.versions.20261009_0036_presales_manual_authorship"
            ).downgrade()

    async with browser_db.engine.begin() as connection:
        await connection.run_sync(downgrade)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        await connection.execute(text("INSERT INTO alembic_version VALUES ('20261009_0035')"))
    return browser_db


async def test_fixed_manual_expansion_and_replay_guard(manual_schema_database):
    with psql_session(manual_schema_database, manual=True) as session:
        assert session.revision(5) == "20261009_0035"
        session.expand(30)
        assert session.revision(5) == "20261009_0036"
        assert session.manual_empty(5)
        with pytest.raises(GuardError, match="preconditions"):
            session.expand(30)
    with psql_session(manual_schema_database, manual=True) as session:
        assert session.revision(5) == "20261009_0036"


async def test_manual_failure_at_revision_update_rolls_back_column(manual_schema_database):
    async with manual_schema_database.engine.begin() as connection:
        await connection.execute(
            text(
                """CREATE FUNCTION refuse_manual_revision() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'interruption'; END $$"""
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER refuse_manual_revision BEFORE UPDATE ON alembic_version "
                "FOR EACH ROW EXECUTE FUNCTION refuse_manual_revision()"
            )
        )
    with psql_session(manual_schema_database, manual=True) as session:
        with pytest.raises(GuardError, match="acknowledgement"):
            session.expand(30)
    with psql_session(manual_schema_database, manual=True) as session:
        assert session.revision(5) == "20261009_0035"


@pytest.mark.parametrize("committed", [False, True])
async def test_lost_manual_migration_receipt_is_observed_without_replay(
    manual_schema_database, committed
):
    with psql_session(manual_schema_database, manual=True) as session:
        sql = (
            "BEGIN;\n"
            + MANUAL_MIGRATION_BODY
            + ("\nCOMMIT;" if committed else "")
            + "\nSELECT pg_sleep(0.5);"
        )
        with pytest.raises(GuardError, match="acknowledgement"):
            session.query(sql, 0.1)
    with psql_session(manual_schema_database, manual=True) as recovered:
        assert recovered.revision(5) == ("20261009_0036" if committed else "20261009_0035")


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE presales_rows DROP CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid;",
        "ALTER TABLE presales_rows ALTER COLUMN manual_authorship SET DEFAULT '{}'::jsonb;",
        "ALTER TABLE presales_rows ALTER COLUMN manual_authorship SET NOT NULL;",
        "ALTER TABLE presales_rows DROP CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid; "
        "ALTER TABLE presales_rows ADD CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid CHECK (manual_authorship IS NULL);",
        "ALTER TABLE presales_rows DROP CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid; "
        "ALTER TABLE presales_rows ADD CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid "
        "CHECK (manual_authorship IS NULL OR (jsonb_typeof(manual_authorship) = 'object' "
        "AND draft IS NOT NULL AND jsonb_typeof(draft) = 'object' AND revision >= 1)) NOT VALID;",
        "ALTER TABLE presales_packets DROP CONSTRAINT ck_presales_packets_presales_workbook_valid;",
        "ALTER TABLE presales_reviews DROP CONSTRAINT "
        "ck_presales_reviews_presales_review_changes_object;",
    ],
)
async def test_manual_expansion_checks_all_inherited_and_new_schema_shapes(
    manual_schema_database, sql
):
    with psql_session(manual_schema_database, manual=True) as session:
        session.expand(30)
        session.query(sql, 5)
        with pytest.raises(GuardError, match="schema"):
            session.revision(5)


async def test_release_guards_preserve_real_reviewed_human_response_and_workbook(
    manual_schema_database, workspace
):
    from scripts.release_switch import ReleaseCluster, ReleasePlan
    from tests.deployment.test_manual_release_switch import manual_switch_data
    from tests.deployment.test_release_switch import Boundary

    with psql_session(manual_schema_database, manual=True) as session:
        session.expand(30)
    service, _, context, _, gateway, payload = workspace
    packet = await service.import_workbook(
        context.principal, import_request(payload, 1), "release-manual"
    )
    evidence = await service.manual_evidence(
        context.principal, packet.id, payload.sources[0].version_id
    )
    request = ManualResponseInput(
        expected_revision=0,
        status="supported",
        answer="Human verified the literal source.",
        citations=[
            evidence.items[0].model_dump(include={"chunk_id", "document_version_id", "excerpt"})
        ],
        prerequisites=[],
        note="Release fixture source verified",
    )
    saved = await service.manual_response(
        context.principal, packet.id, packet.rows[0].id, request, "manual"
    )
    reviewed = await service.review(
        context.principal,
        packet.id,
        packet.rows[0].id,
        ReviewInput(
            expected_revision=1,
            status="supported",
            answer="Reviewed human answer.",
            prerequisites=[],
            note="Separate review",
        ),
        "review",
    )
    before = await service.export_workbook(context.principal, packet.id, "reviewed")
    csv_before = await service.export(context.principal, packet.id, "reviewed")
    with psql_session(manual_schema_database, manual=True) as database:
        assert not database.manual_empty(5)
        for compatible in [False, True]:
            data = manual_switch_data(original=compatible)
            boundary = Boundary(data)
            cluster = ReleaseCluster(
                ReleasePlan(data),
                run=boundary,
                revision=database.revision,
                idle=database.idle,
                workbook_empty=lambda timeout: False,
                manual_empty=database.manual_empty,
                clock=lambda: 1,
            )
            if compatible:
                cluster.apply(100)
                cluster.restore(100)
                cluster.verify(False, 100)
            else:
                with pytest.raises(GuardError, match="manual history"):
                    cluster.restore(100)
                assert not boundary.writes
        assert database.revision(5) == "20261009_0036"
    actual = await service.get(context.principal, packet.id)
    assert actual.rows[0] == reviewed.rows[0]
    assert actual.rows[0].draft == saved.rows[0].draft
    assert actual.workbook == packet.workbook
    assert await service.export_workbook(context.principal, packet.id, "reviewed") == before
    assert await service.export(context.principal, packet.id, "reviewed") == csv_before
    assert not gateway.calls
