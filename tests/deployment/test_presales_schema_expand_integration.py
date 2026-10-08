from __future__ import annotations

import shutil
import subprocess
import time
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from scripts.maintenance_guard import GuardError
from sqlalchemy import text
from tests.agent.test_agent_run_integration import _seed_agent_context
from tests.browser_sessions.conftest import browser_db as browser_db

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.jobs.models import Job

pytestmark = pytest.mark.integration


@pytest.fixture
async def schema_database(browser_db):
    def downgrade(connection):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            for suffix in [
                "20261008_0034_presales_execution_policy",
                "20261008_0033_presales_review_changes",
            ]:
                import_module("enterprise_doc_core.db.migrations.versions." + suffix).downgrade()

    async with browser_db.engine.begin() as connection:
        await connection.run_sync(downgrade)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        await connection.execute(text("INSERT INTO alembic_version VALUES ('20261005_0032')"))
    return browser_db


def psql_session(database, **overrides):
    from scripts.presales_schema_expand import PsqlSession

    url = FoundationSettings().database.url.get_secret_value()
    assert database.schema.startswith("browser_test_")
    if shutil.which("psql"):
        return PsqlSession(url, schema=database.schema, **overrides)
    command = ["docker", "exec", "-i"]
    for name in [
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGOPTIONS",
        "PGAPPNAME",
    ]:
        command.extend(["--env", name])
    command.extend(["enterprise-doc-agent-postgres-1", "psql"])
    return PsqlSession(url, schema=database.schema, command=tuple(command), **overrides)


async def test_session_enforces_limits_when_pooler_ignores_startup_options(
    schema_database, monkeypatch
):
    popen = subprocess.Popen

    def without_startup_options(*args, **kwargs):
        environment = dict(kwargs["env"])
        environment["PGOPTIONS"] = (
            "-c statement_timeout=0 -c lock_timeout=0 -c idle_session_timeout=0"
        )
        return popen(*args, **(kwargs | {"env": environment}))

    monkeypatch.setattr(subprocess, "Popen", without_startup_options)
    with psql_session(schema_database) as session:
        for setting, expected in [
            ("statement_timeout", "10s"),
            ("lock_timeout", "5s"),
            ("idle_session_timeout", "10min"),
        ]:
            assert session.query("SHOW " + setting + ";", 5) == expected
        assert session.query("SELECT current_schema();", 5) == schema_database.schema
        assert session.revision(5) == "20261005_0032"


async def test_fixed_expansion_commits_both_revisions_and_preserves_original_tables(
    schema_database,
):
    database = schema_database
    async with database.engine.connect() as connection:
        before = set(
            await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
            )
        )
    with psql_session(database) as session:
        assert session.revision(5) == "20261005_0032"
        assert session.idle(5)
        session.expand(30)
        assert session.revision(5) == "20261008_0034"
    async with database.engine.connect() as connection:
        after = set(
            await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
            )
        )
        assert after == before
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "20261008_0034"
        )


async def test_error_at_last_version_update_rolls_back_both_columns(schema_database):
    database = schema_database
    async with database.engine.begin() as connection:
        await connection.execute(
            text("""CREATE FUNCTION refuse_final_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.version_num = '20261008_0034' THEN RAISE EXCEPTION 'test interruption'; END IF;
          RETURN NEW;
        END $$""")
        )
        await connection.execute(
            text("""CREATE TRIGGER refuse_final_revision BEFORE UPDATE
        ON alembic_version FOR EACH ROW EXECUTE FUNCTION refuse_final_revision()""")
        )
    with psql_session(database) as session:
        with pytest.raises(GuardError, match="acknowledgement"):
            session.expand(30)
    with psql_session(database) as recovered:
        assert recovered.revision(5) == "20261005_0032"


@pytest.mark.parametrize("committed", [False, True])
async def test_lost_database_receipt_is_reconciled_under_the_same_lock(schema_database, committed):
    from scripts.presales_schema_expand import MIGRATION_BODY

    with psql_session(schema_database) as session:
        # Exercise the process boundary with actual DDL and a deliberately missing
        # timely receipt. Recovery must reconcile, not resend the migration.
        sql = "BEGIN;\n" + MIGRATION_BODY
        sql += "\nCOMMIT;" if committed else ""
        sql += "\nSELECT pg_sleep(0.5);"
        with pytest.raises(GuardError, match="acknowledgement"):
            session.query(sql, 0.1)
    with psql_session(schema_database) as recovered:
        expected = "20261008_0034" if committed else "20261005_0032"
        assert recovered.revision(5) == expected
        if committed:
            with pytest.raises(GuardError, match="preconditions"):
                recovered.expand(10)


async def test_database_session_lock_blocks_a_second_controller_until_released(schema_database):
    with psql_session(schema_database) as first:
        started = time.monotonic()
        with pytest.raises(GuardError, match="lock unavailable"):
            with psql_session(schema_database, connect_timeout=0.1):
                pytest.fail("second session acquired the held migration lock")
        assert time.monotonic() - started < 8
        assert first.revision(5) == "20261005_0032"
    with psql_session(schema_database) as recovered:
        assert recovered.revision(5) == "20261005_0032"


@pytest.mark.parametrize(
    "sql", ["SELECT repeat('sensitive-marker', 6000);", "SELECT pg_sleep(0.5);"]
)
async def test_psql_output_and_wait_are_bounded_without_exposing_raw_content(schema_database, sql):
    with psql_session(schema_database) as session:
        started = time.monotonic()
        with pytest.raises(GuardError, match="acknowledgement") as failure:
            session.query(sql, 0.1)
        assert "sensitive-marker" not in str(failure.value)
        assert time.monotonic() - started < 8
        assert session.process is None
    with psql_session(schema_database) as recovered:
        assert recovered.revision(5) == "20261005_0032"


@pytest.mark.parametrize("case", ["missing_check", "wrong_type", "revision_only", "partial_column"])
async def test_database_shape_drift_is_rejected(schema_database, case):
    with psql_session(schema_database) as session:
        if case in {"missing_check", "wrong_type"}:
            session.expand(30)
            session.query(
                "ALTER TABLE presales_attempts DROP CONSTRAINT "
                "ck_presales_attempts_presales_attempt_execution_policy_object;",
                5,
            )
            if case == "wrong_type":
                session.query(
                    "ALTER TABLE presales_attempts ALTER COLUMN execution_policy TYPE text;", 5
                )
        elif case == "revision_only":
            session.query("UPDATE alembic_version SET version_num='20261008_0034';", 5)
        else:
            session.query("ALTER TABLE presales_attempts ADD COLUMN execution_policy JSONB;", 5)
        with pytest.raises(GuardError, match="schema"):
            session.revision(5)


async def test_pending_business_prevents_expansion_and_keeps_its_job(schema_database):
    database = schema_database
    context = await _seed_agent_context(database.sessions)
    job_id = uuid4()
    async with database.sessions.begin() as session:
        session.add(
            Job(
                id=job_id,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                type="presales.generate",
                status="pending",
                idempotency_key="active-job",
                request_fingerprint="0" * 64,
                payload={},
            )
        )
    with psql_session(database) as session:
        assert not session.idle(5)
        with pytest.raises(GuardError, match="preconditions"):
            session.expand(20)
    with psql_session(database) as recovered:
        assert recovered.revision(5) == "20261005_0032"
    async with database.sessions() as session:
        assert (await session.get(Job, job_id)).status == "pending"


async def test_supervised_window_recovers_original_apps_after_migration_and_lost_start_receipt(
    schema_database,
):
    from scripts.maintenance_guard import Target, Tick
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan
    from scripts.release_switch import Switch
    from tests.deployment.test_presales_schema_expand import expansion_data
    from tests.deployment.test_release_switch import Boundary

    plan = ExpansionPlan(expansion_data())
    boundary = Boundary(plan.data)
    lost_receipt = [False]

    def cluster_run(args, payload, timeout):
        result = boundary(args, payload, timeout)
        if (
            not lost_receipt[0]
            and args[:3] == ["patch", "deployment", "enterprise-doc-api"]
            and '"path": "/spec/replicas", "value": 1' in (payload or "")
        ):
            lost_receipt[0] = True
            raise TimeoutError("synthetic lost Kubernetes reply")
        return result

    def clock():
        return Tick(boot_id="test-boot", elapsed=1, wall=1)

    target = Target(plan.operation, plan.executor, plan.namespace_uid, "a" * 64)
    with TemporaryDirectory(prefix="schema-switch-test-") as directory:
        switch = Switch(Path(directory) / "state.json", clock=clock)
        switch.arm(target)
        original_deadline = switch.status()["deadline"]

        def apply(deadline):
            with psql_session(schema_database) as database:
                ExpansionCluster(plan, database, run=cluster_run, clock=lambda: 1).apply(deadline)

        def restore(deadline):
            with psql_session(schema_database) as database:
                assert database.revision(5) == "20261008_0034"
                ExpansionCluster(plan, database, run=cluster_run, clock=lambda: 1).restore(deadline)

        result = switch.execute(target, apply=apply, restore=restore)
        assert lost_receipt[0] and result["phase"] == "restored"
        assert result["reason"] == "apply_failed" and result["deadline"] == original_deadline
        before = list(boundary.writes)
        assert switch.execute(target, apply=apply, restore=restore) == result
        assert boundary.writes == before
    with psql_session(schema_database) as database:
        assert database.revision(5) == "20261008_0034"
        ExpansionCluster(plan, database, run=boundary, clock=lambda: 1).verify(False, 100)
