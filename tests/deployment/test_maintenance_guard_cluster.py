from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.validate_staging_prerequisites import REQUIRED_APPROVAL_ANNOTATIONS

NS = "enterprise-doc-agent-staging"
SERVICES = ("api", "worker", "consumer", "web")


def plan_data() -> dict[str, Any]:
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NS,
            "uid": "namespace-uid",
            "resourceVersion": "1",
            "annotations": dict.fromkeys(REQUIRED_APPROVAL_ANNOTATIONS, "a" * 64),
        },
    }
    config = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "enterprise-doc-config",
            "namespace": NS,
            "uid": "config-uid",
            "resourceVersion": "1",
        },
        "data": {"EXISTING_SETTING": "keep"},
    }
    candidate = copy.deepcopy([namespace, config])
    for suffix in ("api-images", "worker-images", "consumer-images", "web-images", "config-sha256"):
        candidate[0]["metadata"]["annotations"][f"enterprise-doc-agent/approved-{suffix}"] = (
            "b" * 64
        )
    candidate[0]["metadata"]["annotations"]["enterprise-doc-agent/prerequisites-sha256"] = "b" * 64
    candidate[1]["data"].update(
        {
            "PRESALES__AUTOMATIC_FAILOVER_ENABLED": "false",
            "PRESALES__BACKGROUND_GENERATION_ENABLED": "false",
            "PRESALES__DAILY_DISPATCH_LIMIT": "200",
            "PRESALES__QUEUE_TIMEOUT_SECONDS": "900",
            "PRESALES__ROUTE_COOLDOWN_SECONDS": "30",
            "PRESALES__ROUTE_FAILURE_THRESHOLD": "3",
        }
    )
    deployments = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"enterprise-doc-{name}",
                "namespace": NS,
                "uid": f"{name}-uid",
                "resourceVersion": "1",
            },
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "containers": [{"name": name, "image": f"test/{name}@sha256:{'a' * 64}"}]
                    }
                },
            },
        }
        for name in SERVICES
    ]
    return {
        "schema_version": 1,
        "operation": "window-one",
        "executor": "a" * 40,
        "namespace_uid": "namespace-uid",
        "original_revision": "20260923_0027",
        "candidate_sha256": "c" * 64,
        "original_prerequisites": [namespace, config],
        "candidate_prerequisites": candidate,
        "deployments": deployments,
        "jobs": [],
    }


class ClusterBoundary:
    def __init__(self, data: dict[str, Any]) -> None:
        self.prerequisites = copy.deepcopy(data["candidate_prerequisites"])
        self.deployments = copy.deepcopy(data["deployments"])
        for item in self.deployments:
            item["spec"]["replicas"] = 0
        self.jobs: list[dict[str, Any]] = []
        self.pods: list[dict[str, Any]] = []
        self.schedulers: list[dict[str, Any]] = []
        self.calls: list[list[str]] = []
        self.writes: list[tuple[str, str]] = []
        self.fail_rollout = False

    def __call__(self, args: list[str], payload: str | None, timeout: float) -> str:
        assert 0 < timeout <= 10
        self.calls.append(args)
        if args[0] == "get":
            if args[1] == "namespace":
                return json.dumps(self.prerequisites[0])
            items = {
                "deployments": self.deployments,
                "jobs": self.jobs,
                "pods": self.pods,
                "hpa,cronjobs,statefulsets,daemonsets": self.schedulers,
            }.get(args[1], self.prerequisites[1:])
            return json.dumps({"kind": "List", "items": items})
        if args[0] == "patch":
            assert payload
            kind, name = args[1:3]
            items = self.deployments if kind == "deployment" else self.prerequisites
            target = next(i for i in items if i["metadata"]["name"] == name)
            patches = json.loads(payload)
            assert patches[0] == {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": target["metadata"]["resourceVersion"],
            }
            for patch in patches[1:]:
                parts = patch["path"].strip("/").split("/")
                dest = target
                for part in parts[:-1]:
                    dest = dest[part]
                dest[parts[-1]] = patch["value"]
            target["metadata"]["resourceVersion"] = str(
                int(target["metadata"]["resourceVersion"]) + 1
            )
            self.writes.append((kind, name))
            return json.dumps(target)
        if args[0] == "rollout":
            if self.fail_rollout:
                raise TimeoutError("private upstream failure")
            return "ready"
        raise AssertionError(args)


def test_recovery_validates_whole_plan_and_restores_web_last(tmp_path: Path) -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    cluster = Cluster(
        Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
    )
    cluster.restore(60)
    assert boundary.prerequisites == data["original_prerequisites"] or all(
        item["data"] == data["original_prerequisites"][1]["data"]
        for item in boundary.prerequisites
        if item["kind"] == "ConfigMap"
    )
    assert (
        boundary.prerequisites[0]["metadata"]["annotations"]
        == data["original_prerequisites"][0]["metadata"]["annotations"]
    )
    assert boundary.writes[-1] == ("deployment", "enterprise-doc-web")
    assert all(d["spec"]["replicas"] == 1 for d in boundary.deployments)


@pytest.mark.parametrize(
    "drift",
    [
        "namespace",
        "annotations",
        "config",
        "template",
        "deployment-uid",
        "job",
        "pod",
        "scheduler",
        "revision",
        "missing",
    ],
)
def test_recovery_refuses_drift_before_any_mutation(drift: str) -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    revision = "20260923_0027"
    if drift == "namespace":
        boundary.prerequisites[0]["metadata"]["uid"] = "another-cluster"
    elif drift == "annotations":
        boundary.prerequisites[0]["metadata"]["annotations"]["unapproved"] = "changed"
    elif drift == "config":
        boundary.prerequisites[1]["data"]["EXISTING_SETTING"] = "changed"
    elif drift == "template":
        boundary.deployments[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "new"
    elif drift == "deployment-uid":
        boundary.deployments[0]["metadata"]["uid"] = "recreated"
    elif drift == "job":
        boundary.jobs = [
            {"metadata": {"name": "enterprise-doc-migrate", "uid": "new"}, "status": {"active": 1}}
        ]
    elif drift == "pod":
        boundary.pods = [
            {
                "metadata": {
                    "labels": {"app.kubernetes.io/name": "enterprise-doc-api"},
                    "ownerReferences": [{"kind": "Job"}],
                },
                "status": {"phase": "Running"},
            }
        ]
    elif drift == "scheduler":
        boundary.schedulers = [{"kind": "HorizontalPodAutoscaler"}]
    elif drift == "revision":
        revision = "20260924_0031"
    else:
        boundary.prerequisites.pop()
    cluster = Cluster(
        Plan(data), run=boundary, revision=lambda timeout: revision, clock=lambda: 1.0
    )
    with pytest.raises(ValueError):
        cluster.restore(60)
    assert not boundary.writes


@pytest.mark.parametrize("partial", ["config-only", "namespace-only", "some-backends-restored"])
def test_partial_recovery_can_be_revalidated_and_completed(partial: str) -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    if partial == "config-only":
        boundary.prerequisites[0] = copy.deepcopy(data["original_prerequisites"][0])
    elif partial == "namespace-only":
        boundary.prerequisites[1] = copy.deepcopy(data["original_prerequisites"][1])
    else:
        boundary.deployments[0]["spec"]["replicas"] = 1
    Cluster(
        Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
    ).restore(60)
    assert all(d["spec"]["replicas"] == 1 for d in boundary.deployments)
    assert boundary.writes[-1] == ("deployment", "enterprise-doc-web")


def test_staging_updates_config_and_namespace_and_rejects_omitted_approval() -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    bad = copy.deepcopy(data)
    bad["candidate_prerequisites"][0] = copy.deepcopy(data["original_prerequisites"][0])
    with pytest.raises(ValueError, match="Namespace"):
        Plan(bad)
    boundary = ClusterBoundary(data)
    boundary.prerequisites = copy.deepcopy(data["original_prerequisites"])
    cluster = Cluster(
        Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
    )
    cluster.stage(60)
    assert boundary.writes == [("configmap", "enterprise-doc-config"), ("namespace", NS)]
    assert boundary.prerequisites[1]["data"] == data["candidate_prerequisites"][1]["data"]
    assert (
        boundary.prerequisites[0]["metadata"]["annotations"]
        == data["candidate_prerequisites"][0]["metadata"]["annotations"]
    )


def test_backend_failure_keeps_web_paused_and_no_database_writes() -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    boundary.fail_rollout = True
    with pytest.raises(TimeoutError):
        Cluster(
            Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
        ).restore(60)
    assert boundary.deployments[-1]["spec"]["replicas"] == 0
    assert not any(name == "enterprise-doc-web" for _, name in boundary.writes)


def test_pausing_and_staging_require_original_configuration_and_closed_entry() -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    boundary.prerequisites = copy.deepcopy(data["original_prerequisites"])
    boundary.deployments = copy.deepcopy(data["deployments"])
    cluster = Cluster(
        Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
    )
    cluster.check_original(60)
    with pytest.raises(ValueError, match="close"):
        cluster.pause_backends(60)
    assert not boundary.writes
    cluster.pause_web(60)
    with pytest.raises(ValueError, match="paused"):
        cluster.stage(60)
    cluster.pause_backends(60)
    cluster.stage(60)
    assert all(d["spec"]["replicas"] == 0 for d in boundary.deployments)


def test_cluster_budget_and_resource_version_failures_do_not_restore_web() -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    clock = [1.0]

    def revision(timeout: float) -> str:
        clock[0] = 61
        return "20260923_0027"

    with pytest.raises(ValueError, match="budget"):
        Cluster(Plan(data), run=boundary, revision=revision, clock=lambda: clock[0]).restore(60)
    assert not boundary.writes
    boundary.prerequisites[1]["metadata"].pop("resourceVersion")
    with pytest.raises(ValueError, match="resourceVersion"):
        Cluster(
            Plan(data), run=boundary, revision=lambda timeout: "20260923_0027", clock=lambda: 1.0
        ).restore(60)
    assert not boundary.writes


def test_readiness_wait_uses_total_budget_and_retries_only_reads() -> None:
    from scripts.maintenance_guard_cluster import Cluster, Plan

    data = plan_data()
    boundary = ClusterBoundary(data)
    tick = [0.0]

    def run(args: list[str], payload: str | None, timeout: float) -> str:
        if args[0] == "rollout":
            tick[0] += 11
            raise subprocess.TimeoutExpired("kubectl", timeout)
        return boundary(args, payload, timeout)

    with pytest.raises(ValueError, match="budget"):
        Cluster(
            Plan(data), run=run, revision=lambda timeout: "20260923_0027", clock=lambda: tick[0]
        ).restore(20)
    assert [name for kind, name in boundary.writes if kind == "deployment"] == [
        "enterprise-doc-api"
    ]
    assert boundary.deployments[-1]["spec"]["replicas"] == 0


def test_database_probe_is_read_only_bounded_and_keeps_dsn_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.maintenance_guard_cluster import database_revision

    monkeypatch.setenv(
        "MAINTENANCE_GUARD_DATABASE_URL",
        "postgresql://guard:p%40ss%3Aword@db.invalid:5432/app?sslmode=require",
    )

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args[0] == "psql"
        assert "p@ss:word" not in " ".join(args)
        assert "BEGIN READ ONLY" in args[-1]
        assert "statement_timeout" in args[-1]
        assert "SELECT version_num FROM public.alembic_version" in args[-1]
        assert "--no-psqlrc" in args and "--no-password" in args
        assert kwargs["env"]["PGHOST"] == "db.invalid"
        assert kwargs["env"]["PGPASSWORD"] == "p@ss:word"
        assert kwargs["env"]["PGSSLMODE"] == "require"
        assert "default_transaction_read_only=on" in kwargs["env"]["PGOPTIONS"]
        assert kwargs["timeout"] == 3
        return subprocess.CompletedProcess(args, 0, stdout="20260923_0027\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert database_revision(3) == "20260923_0027"
