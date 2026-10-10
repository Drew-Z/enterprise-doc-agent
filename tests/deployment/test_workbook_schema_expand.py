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


def workbook_expansion_data():
    from scripts.presales_schema_expand import WORKBOOK_MIGRATION_BODY

    data = expansion_data()
    data.update(
        original_revision="20261008_0034",
        target_revision="20261009_0035",
        migration_sha256=hashlib.sha256(WORKBOOK_MIGRATION_BODY.encode()).hexdigest(),
    )
    return data


def test_workbook_expansion_is_fixed_and_retains_original_resources():
    data = workbook_expansion_data()
    plan = ExpansionPlan(data)
    assert plan.accepted_revisions() == ("20261008_0034", "20261009_0035")
    assert plan.original == plan.candidate and plan.deployments == plan.desired
    database = DatabaseBoundary("20261008_0034")

    def expand(timeout):
        assert timeout > 0
        database.current = "20261009_0035"
        database.expansions += 1

    database.expand = expand
    boundary = Boundary(data)
    cluster = ExpansionCluster(plan, database, run=boundary, clock=lambda: 1.0)
    cluster.apply(100)
    assert database.expansions == 1
    cluster.verify(False, 100)
    cluster.restore(100)
    assert database.current == "20261009_0035" and database.expansions == 1
    for item in data["deployments"]:
        live = next(x for x in boundary.items if x["metadata"]["name"] == item["metadata"]["name"])
        assert live["spec"] == item["spec"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_revision", "20261005_0032"),
        ("target_revision", "head"),
        ("migration_sha256", "0" * 64),
        ("sql", "DROP TABLE presales_packets"),
    ],
)
def test_workbook_expansion_refuses_unknown_migration_or_sql(field, value):
    data = workbook_expansion_data()
    data[field] = value
    with pytest.raises(GuardError):
        ExpansionPlan(data)


def test_workbook_fixed_sql_matches_actual_alembic_output():
    from scripts.presales_schema_expand import WORKBOOK_MIGRATION_BODY

    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", "upgrade", "20261008_0034:20261009_0035", "--sql"],
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

    assert statements(result.stdout) == statements(WORKBOOK_MIGRATION_BODY)


def test_workbook_plan_refuses_application_changes():
    data = workbook_expansion_data()
    data["candidate_deployments"] = copy.deepcopy(data["deployments"])
    data["candidate_deployments"][0]["spec"]["replicas"] = 0
    with pytest.raises(GuardError):
        ExpansionPlan(data)
