from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.maintenance_guard import GuardError
from tests.deployment.test_release_switch import Boundary, upload_switch_data

ROOT = Path(__file__).resolve().parents[2]


def expansion_data():
    from scripts.presales_schema_expand import MIGRATION_BODY

    data = upload_switch_data()
    data.update(
        schema_version=4,
        release_kind="presales_schema_expand",
        target_revision="20261008_0034",
        migration_sha256=hashlib.sha256(MIGRATION_BODY.encode()).hexdigest(),
    )
    data["candidate_prerequisites"] = copy.deepcopy(data["original_prerequisites"])
    data["candidate_deployments"] = copy.deepcopy(data["deployments"])
    return data


def test_additive_schema_plan_retains_all_running_resources():
    from scripts.presales_schema_expand import ExpansionPlan

    plan = ExpansionPlan(expansion_data())
    assert plan.revision == "20261005_0032"
    assert plan.accepted_revisions() == ("20261005_0032", "20261008_0034")
    assert plan.original == plan.candidate
    assert plan.deployments == plan.desired


@pytest.mark.parametrize(
    "mutation",
    [
        "original_revision",
        "target_revision",
        "schema_version",
        "release_kind",
        "sql",
        "migration_sha256",
        "image",
        "config",
        "approval",
        "credential",
        "pool",
        "replicas",
    ],
)
def test_expansion_plan_rejects_other_migrations_and_application_changes(mutation):
    from scripts.presales_schema_expand import ExpansionPlan

    data = expansion_data()
    if mutation == "original_revision":
        data[mutation] = "20261008_0033"
    elif mutation == "target_revision":
        data[mutation] = "head"
    elif mutation == "schema_version":
        data[mutation] = 2
    elif mutation == "release_kind":
        data[mutation] = "images_only"
    elif mutation == "sql":
        data[mutation] = "DROP TABLE presales_attempts"
    elif mutation == "migration_sha256":
        data[mutation] = "0" * 64
    elif mutation == "image":
        data["candidate_deployments"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "ghcr.io/drew-z/enterprise-doc-api@sha256:" + "b" * 64
        )
    elif mutation == "config":
        data["candidate_prerequisites"][1]["data"]["UPLOAD__SINGLE_PUT_ENABLED"] = "true"
    elif mutation == "approval":
        data["candidate_prerequisites"][0]["metadata"]["annotations"][
            "enterprise-doc-agent/prerequisites-sha256"
        ] = "b" * 64
    elif mutation == "credential":
        data["secret"]["new_key"] = "bmV3"
    elif mutation == "pool":
        data["api_database_pool_size"] = 2
    else:
        data["candidate_deployments"][0]["spec"]["replicas"] = 0
    with pytest.raises(GuardError):
        ExpansionPlan(data)


def test_fixed_sql_matches_the_real_alembic_two_revision_output():
    from scripts.presales_schema_expand import MIGRATION_BODY

    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", "upgrade", "20261005_0032:20261008_0034", "--sql"],
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
        value = re.sub(r"--[^\n]*", "", value)
        return [
            re.sub(r"\s+", "", statement)
            for statement in value.split(";")
            if statement.strip() and statement.strip() not in {"BEGIN", "COMMIT"}
        ]

    assert statements(result.stdout) == statements(MIGRATION_BODY)


class DatabaseBoundary:
    def __init__(self, revision="20261005_0032", *, fail_after_commit=False):
        self.current = revision
        self.pending = False
        self.expansions = 0
        self.fail_after_commit = fail_after_commit

    def revision(self, timeout):
        assert timeout > 0
        return self.current

    def idle(self, timeout):
        assert timeout > 0
        return not self.pending

    def expand(self, timeout):
        assert 0 < timeout <= 60
        self.expansions += 1
        self.current = "20261008_0034"
        if self.fail_after_commit:
            raise GuardError("unknown acknowledgement")


def test_schema_window_migrates_once_and_reopens_only_the_original_applications():
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan

    data = expansion_data()
    boundary, database = Boundary(data), DatabaseBoundary()
    cluster = ExpansionCluster(ExpansionPlan(data), database, run=boundary, clock=lambda: 1.0)
    cluster.apply(100)
    assert database.expansions == 1
    cluster.verify(False, 100)
    assert all(kind == "deployment" for kind, _ in boundary.writes)
    assert [name for _, name in boundary.writes[:4]] == [
        "enterprise-doc-web",
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
    ]
    for expected in data["deployments"]:
        current = next(
            i for i in boundary.items if i["metadata"]["name"] == expected["metadata"]["name"]
        )
        assert current["spec"] == expected["spec"]


def test_schema_cli_validates_pinned_sources_without_opening_a_database(tmp_path):
    data = expansion_data()
    names = (
        "presales_schema_expand.py",
        "release_switch.py",
        "maintenance_guard.py",
        "maintenance_guard_cluster.py",
        "backup_database.py",
        "render_k8s_phase.py",
        "validate_staging_prerequisites.py",
    )
    data["executor_sources"] = {
        name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() for name in names
    }
    plan = tmp_path / "private-plan.json"
    plan.write_text(json.dumps(data), encoding="utf-8")
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    args = [
        sys.executable,
        "-B",
        "-m",
        "scripts.presales_schema_expand",
        "validate",
        "--plan",
        str(plan),
        "--plan-sha256",
        digest,
    ]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "valid",
        "original_revision": "20261005_0032",
        "target_revision": "20261008_0034",
        "migration": "fixed_additive",
    }
    data["executor_sources"]["presales_schema_expand.py"] = "0" * 64
    plan.write_text(json.dumps(data), encoding="utf-8")
    args[-1] = hashlib.sha256(plan.read_bytes()).hexdigest()
    refused = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert refused.returncode == 1
    assert "refused" in refused.stderr and "old_key" not in refused.stderr


@pytest.mark.parametrize("revision", ["20261005_0032", "20261008_0034"])
def test_schema_recovery_keeps_complete_revision_and_original_images(revision):
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan

    data = expansion_data()
    boundary, database = Boundary(data), DatabaseBoundary(revision)
    for item in boundary.items:
        if item["kind"] == "Deployment" and item["metadata"]["name"] != "enterprise-doc-web":
            item["spec"]["replicas"] = 0
    cluster = ExpansionCluster(ExpansionPlan(data), database, run=boundary, clock=lambda: 1.0)
    cluster.restore(100)
    cluster.verify(False, 100)
    assert database.current == revision and database.expansions == 0
    assert all(kind == "deployment" for kind, _ in boundary.writes)


@pytest.mark.parametrize("phase", ["apply", "restore"])
@pytest.mark.parametrize("revision", ["20261008_0033", "unrecognized"])
def test_schema_unknown_or_intermediate_revision_causes_no_cluster_write(phase, revision):
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan

    data = expansion_data()
    boundary, database = Boundary(data), DatabaseBoundary(revision)
    cluster = ExpansionCluster(ExpansionPlan(data), database, run=boundary, clock=lambda: 1.0)
    with pytest.raises(GuardError):
        getattr(cluster, phase)(100)
    assert not boundary.writes and database.expansions == 0


@pytest.mark.parametrize("phase", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_schema_window_refuses_business_before_or_during_close(phase, during_close):
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan

    data = expansion_data()
    boundary, database = Boundary(data), DatabaseBoundary()
    database.pending = not during_close

    def run(args, payload, timeout):
        result = boundary(args, payload, timeout)
        if during_close and args[0] == "patch":
            database.pending = True
        return result

    cluster = ExpansionCluster(ExpansionPlan(data), database, run=run, clock=lambda: 1.0)
    with pytest.raises(GuardError):
        getattr(cluster, phase)(100)
    assert database.expansions == 0
    if during_close:
        assert all(
            item["spec"]["replicas"] == 0 for item in boundary.items if item["kind"] == "Deployment"
        )
    else:
        assert not boundary.writes


def test_schema_replay_refuses_completed_migration_without_writes():
    from scripts.presales_schema_expand import ExpansionCluster, ExpansionPlan

    data = expansion_data()
    boundary, database = Boundary(data), DatabaseBoundary("20261008_0034")
    cluster = ExpansionCluster(ExpansionPlan(data), database, run=boundary, clock=lambda: 1.0)
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes and database.expansions == 0
