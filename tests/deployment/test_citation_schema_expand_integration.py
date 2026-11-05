from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from scripts.maintenance_guard import GuardError
from scripts.presales_schema_expand import CITATION_MIGRATION_BODY, ExpansionCluster, ExpansionPlan
from scripts.release_switch import ReleaseCluster, ReleasePlan
from sqlalchemy import text
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.deployment.test_citation_release_switch import citation_switch_data
from tests.deployment.test_citation_schema_expand import citation_expansion_data
from tests.deployment.test_presales_schema_expand_integration import psql_session
from tests.deployment.test_release_switch import Boundary
from tests.presales.test_presales_workflow_integration import workspace as workspace
from tests.presales.test_workbook_integration import import_request

from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.presales.schemas import ManualResponseInput, ReviewInput

pytestmark = pytest.mark.integration


@pytest.fixture
async def citation_schema_database(browser_db):
    def downgrade(connection):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            import_module(
                "enterprise_doc_core.db.migrations.versions.20261010_0037_presales_review_citations"
            ).downgrade()

    async with browser_db.engine.begin() as connection:
        await connection.run_sync(downgrade)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        await connection.execute(text("INSERT INTO alembic_version VALUES ('20261009_0036')"))
    return browser_db


async def test_fixed_citation_expansion_and_replay_guard(citation_schema_database):
    with psql_session(citation_schema_database, citations=True) as session:
        assert session.revision(5) == "20261009_0036"
        session.expand(30)
        assert session.revision(5) == "20261010_0037"
        assert session.citation_empty(5)
        with pytest.raises(GuardError, match="preconditions"):
            session.expand(30)
    with psql_session(citation_schema_database, citations=True) as recovered:
        assert recovered.revision(5) == "20261010_0037"


async def test_citation_revision_update_failure_rolls_back_column(citation_schema_database):
    async with citation_schema_database.engine.begin() as connection:
        await connection.execute(
            text("""CREATE FUNCTION refuse_citation_revision() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'interruption'; END $$""")
        )
        await connection.execute(
            text(
                "CREATE TRIGGER refuse_citation_revision BEFORE UPDATE ON alembic_version "
                "FOR EACH ROW EXECUTE FUNCTION refuse_citation_revision()"
            )
        )
    with psql_session(citation_schema_database, citations=True) as session:
        with pytest.raises(GuardError, match="acknowledgement"):
            session.expand(30)
    with psql_session(citation_schema_database, citations=True) as recovered:
        assert recovered.revision(5) == "20261009_0036"


@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_citation_commit_is_observed_without_replay(
    citation_schema_database, committed
):
    with psql_session(citation_schema_database, citations=True) as session:
        sql = (
            "BEGIN;\n"
            + CITATION_MIGRATION_BODY
            + ("\nCOMMIT;" if committed else "")
            + "\nSELECT pg_sleep(0.5);"
        )
        with pytest.raises(GuardError, match="acknowledgement"):
            session.query(sql, 0.1)
    with psql_session(citation_schema_database, citations=True) as recovered:
        assert recovered.revision(5) == ("20261010_0037" if committed else "20261009_0036")


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE presales_reviews DROP CONSTRAINT "
        "ck_presales_reviews_presales_review_citations_array;",
        "ALTER TABLE presales_reviews ALTER COLUMN citations SET DEFAULT '[]'::jsonb;",
        "ALTER TABLE presales_reviews ALTER COLUMN citations SET NOT NULL;",
        "ALTER TABLE presales_reviews DROP CONSTRAINT "
        "ck_presales_reviews_presales_review_citations_array; "
        "ALTER TABLE presales_reviews ADD CONSTRAINT "
        "ck_presales_reviews_presales_review_citations_array CHECK (citations IS NULL);",
        "ALTER TABLE presales_reviews DROP CONSTRAINT "
        "ck_presales_reviews_presales_review_citations_array; "
        "ALTER TABLE presales_reviews ADD CONSTRAINT "
        "ck_presales_reviews_presales_review_citations_array "
        "CHECK (citations IS NULL OR CASE WHEN jsonb_typeof(citations) = 'array' "
        "THEN jsonb_array_length(citations) <= 12 ELSE false END) NOT VALID;",
        "ALTER TABLE presales_rows DROP CONSTRAINT "
        "ck_presales_rows_presales_manual_authorship_valid;",
        "ALTER TABLE presales_packets DROP CONSTRAINT ck_presales_packets_presales_workbook_valid;",
        "ALTER TABLE presales_reviews DROP CONSTRAINT "
        "ck_presales_reviews_presales_review_changes_object;",
        "ALTER TABLE presales_attempts DROP CONSTRAINT "
        "ck_presales_attempts_presales_attempt_execution_policy_object;",
    ],
)
async def test_all_inherited_and_new_shapes_are_checked(citation_schema_database, sql):
    with psql_session(citation_schema_database, citations=True) as session:
        session.expand(30)
        session.query(sql, 5)
        with pytest.raises(GuardError, match="schema"):
            session.revision(5)


@pytest.mark.parametrize("empty_selection", [False, True])
async def test_actual_citation_history_blocks_legacy_recovery_and_preserves_delivery(
    citation_schema_database, workspace, empty_selection
):
    with psql_session(citation_schema_database, citations=True) as database:
        database.expand(30)
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.import_workbook(
        context.principal, import_request(payload, 1), "citation-release"
    )
    page = await service.manual_evidence(
        context.principal, packet.id, payload.sources[0].version_id
    )
    original = page.items[0].model_dump(include={"chunk_id", "document_version_id", "excerpt"})
    saved = await service.manual_response(
        context.principal,
        packet.id,
        packet.rows[0].id,
        ManualResponseInput(
            expected_revision=0,
            status="supported",
            answer="Original human answer.",
            citations=[original],
            prerequisites=[],
            note="Original source checked",
        ),
        "manual",
    )
    legacy = await service.review(
        context.principal,
        packet.id,
        packet.rows[0].id,
        ReviewInput(
            expected_revision=1,
            status="supported",
            answer="Legacy review.",
            prerequisites=[],
            note="Original evidence unchanged",
        ),
        "legacy",
    )
    with psql_session(citation_schema_database, citations=True) as database:
        assert not database.manual_empty(5) and database.citation_empty(5)
        data = citation_switch_data()
        boundary = Boundary(data)
        cluster = ReleaseCluster(
            ReleasePlan(data),
            run=boundary,
            revision=database.revision,
            idle=database.idle,
            citation_empty=database.citation_empty,
            clock=lambda: 1,
        )
        cluster.apply(100)
        cluster.restore(100)
    citations = [] if empty_selection else [{**original, "excerpt": "30 days"}]
    corrected = await service.review(
        context.principal,
        packet.id,
        packet.rows[0].id,
        ReviewInput(
            expected_revision=2,
            status="insufficient_evidence" if empty_selection else "supported",
            answer="Review with independent evidence.",
            citations=citations,
            prerequisites=[],
            missing_information=["Need source"] if empty_selection else [],
            note="Corrected source selection",
        ),
        "corrected",
    )
    before_xlsx = await service.export_workbook(context.principal, packet.id, "reviewed")
    before_csv = await service.export(context.principal, packet.id, "reviewed")
    async with sessions() as session:
        before_counts = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM presales_attempts), "
                    "(SELECT count(*) FROM usage_reservations), (SELECT count(*) FROM jobs)"
                )
            )
        ).one()
    with psql_session(citation_schema_database, citations=True) as database:
        assert not database.citation_empty(5)
        for compatible in [False, True]:
            data = citation_switch_data(original=compatible)
            boundary = Boundary(data)
            cluster = ReleaseCluster(
                ReleasePlan(data),
                run=boundary,
                revision=database.revision,
                idle=database.idle,
                citation_empty=database.citation_empty,
                clock=lambda: 1,
            )
            if compatible:
                cluster.apply(100)
                cluster.restore(100)
                cluster.verify(False, 100)
            else:
                with pytest.raises(GuardError, match="citation history"):
                    cluster.restore(100)
                assert not boundary.writes
        expansion = citation_expansion_data()
        boundary = Boundary(expansion)
        cluster = ExpansionCluster(
            ExpansionPlan(expansion), database, run=boundary, clock=lambda: 1
        )
        with pytest.raises(GuardError, match="citation history"):
            cluster.restore(100)
        assert not boundary.writes
        assert database.revision(5) == "20261010_0037"
    reloaded = await service.get(context.principal, packet.id)
    assert reloaded.rows[0] == corrected.rows[0]
    assert reloaded.rows[0].draft == saved.rows[0].draft
    assert reloaded.rows[0].review_history[0] == legacy.rows[0].review
    assert reloaded.workbook == packet.workbook
    assert await service.export_workbook(context.principal, packet.id, "reviewed") == before_xlsx
    assert await service.export(context.principal, packet.id, "reviewed") == before_csv
    assert not gateway.calls
    async with sessions() as session:
        assert (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM presales_attempts), "
                    "(SELECT count(*) FROM usage_reservations), (SELECT count(*) FROM jobs)"
                )
            )
        ).one() == before_counts
