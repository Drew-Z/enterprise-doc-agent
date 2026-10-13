import copy
import hashlib
import os
import re
import subprocess
import sys

import pytest
from scripts.maintenance_guard import GuardError
from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan
from tests.deployment.test_presales_schema_expand import ROOT, DatabaseBoundary, expansion_data
from tests.deployment.test_release_switch import Boundary


def manual_expansion_data():
    from scripts.presales_schema_expand import MANUAL_MIGRATION_BODY

    data = expansion_data()
    data.update(
        original_revision="20261009_0035",
        target_revision="20261009_0036",
        workbook_readers={"original": True, "candidate": True},
        migration_sha256=hashlib.sha256(MANUAL_MIGRATION_BODY.encode()).hexdigest(),
    )
    return data


def test_manual_expansion_retains_original_apps_and_recovers_without_replay():
    data = manual_expansion_data()
    plan = ExpansionPlan(data)
    assert plan.accepted_revisions() == ("20261009_0035", "20261009_0036")
    assert plan.original == plan.candidate and plan.deployments == plan.desired
    database = DatabaseBoundary("20261009_0035")
    database.manual_empty = lambda timeout: True

    def expand(timeout):
        assert timeout > 0
        database.current = "20261009_0036"
        database.expansions += 1

    database.expand = expand
    boundary = Boundary(data)
    cluster = ExpansionCluster(plan, database, run=boundary, clock=lambda: 1)
    cluster.apply(100)
    cluster.verify(False, 100)
    cluster.restore(100)
    cluster.verify(False, 100)
    assert database.current == "20261009_0036" and database.expansions == 1
    assert all(kind == "deployment" for kind, _ in boundary.writes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_revision", "20261008_0034"),
        ("target_revision", "head"),
        ("migration_sha256", "0" * 64),
        ("sql", "DROP TABLE presales_rows"),
        ("workbook_readers", None),
        ("workbook_readers", {"original": True, "candidate": False}),
    ],
)
def test_manual_expansion_refuses_scope_or_capability_drift(field, value):
    data = manual_expansion_data()
    data[field] = value
    with pytest.raises(GuardError):
        ExpansionPlan(data)


def test_manual_expansion_refuses_application_changes():
    data = manual_expansion_data()
    data["candidate_deployments"] = copy.deepcopy(data["deployments"])
    data["candidate_deployments"][0]["spec"]["replicas"] = 0
    with pytest.raises(GuardError):
        ExpansionPlan(data)


def test_earlier_expansion_keeps_rejecting_undeclared_reader_fields():
    data = expansion_data()
    data["workbook_readers"] = None
    with pytest.raises(GuardError):
        ExpansionPlan(data)


def test_manual_fixed_sql_matches_actual_alembic_output():
    from scripts.presales_schema_expand import MANUAL_MIGRATION_BODY

    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", "upgrade", "20261009_0035:20261009_0036", "--sql"],
        cwd=ROOT,
        env=os.environ
        | {
            "APP_ENV": "local",
            "DATABASE__URL": "postgresql+psycopg://offline:offline@127.0.0.1:1/offline",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    def statements(value):
        return [
            re.sub(r"\s+", "", x)
            for x in re.sub(r"--[^\n]*", "", value).split(";")
            if x.strip() and x.strip() not in {"BEGIN", "COMMIT"}
        ]

    assert statements(result.stdout) == statements(MANUAL_MIGRATION_BODY)


@pytest.mark.parametrize("during_close", [False, True])
def test_expansion_recovery_refuses_reopening_old_apps_with_manual_history(during_close):
    data = manual_expansion_data()
    database = DatabaseBoundary("20261009_0036")
    empty = [during_close]
    database.manual_empty = lambda timeout: empty[0]
    boundary = Boundary(data)

    def run(args, payload, timeout):
        result = boundary(args, payload, timeout)
        if args[0] == "patch":
            empty[0] = False
        return result

    cluster = ExpansionCluster(ExpansionPlan(data), database, run=run, clock=lambda: 1)
    with pytest.raises(GuardError, match="manual history"):
        cluster.restore(100)
    assert database.expansions == 0
    if during_close:
        assert all(x["spec"]["replicas"] == 0 for x in boundary.items if x["kind"] == "Deployment")
    else:
        assert not boundary.writes


def test_expansion_recovery_can_reopen_original_schema_without_querying_absent_column():
    data = manual_expansion_data()
    database = DatabaseBoundary("20261009_0035")
    boundary = Boundary(data)
    cluster = ExpansionCluster(ExpansionPlan(data), database, run=boundary, clock=lambda: 1)
    cluster.restore(100)
    cluster.verify(False, 100)
    assert database.expansions == 0
