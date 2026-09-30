from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.maintenance_guard_cluster import canonical_digest
from scripts.validate_staging_prerequisites import REQUIRED_APPROVAL_ANNOTATIONS

NS = "enterprise-doc-agent-staging"
PREFIX = "enterprise-doc-agent/"
NAMES = ("api", "worker", "consumer", "web")


def release_data() -> dict[str, Any]:
    config = {"MODEL__MODEL_NAME": "old-model", "MODEL__BASE_URL": "https://old.invalid/v1"}
    new_config = config | {"MODEL__MODEL_NAME": "new-model"}
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
    namespace["metadata"]["annotations"].update(
        {
            PREFIX + "approved-config-sha256": canonical_digest(config),
            PREFIX + "approved-model-base-url": config["MODEL__BASE_URL"],
            PREFIX + "approved-model-name": config["MODEL__MODEL_NAME"],
        }
    )
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "enterprise-doc-config",
            "namespace": NS,
            "uid": "config-uid",
            "resourceVersion": "1",
        },
        "data": config,
    }
    original = [namespace, cm]
    candidate = copy.deepcopy(original)
    candidate[1]["data"] = new_config
    candidate[0]["metadata"]["annotations"].update(
        {
            PREFIX + "approved-config-sha256": canonical_digest(new_config),
            PREFIX + "approved-model-name": new_config["MODEL__MODEL_NAME"],
            PREFIX + "prerequisites-sha256": "b" * 64,
        }
    )
    deployments = []
    for name in NAMES:
        old_image = f"ghcr.io/drew-z/enterprise-doc-{name}@sha256:{'a' * 64}"
        new_image = f"ghcr.io/drew-z/enterprise-doc-{name}@sha256:{'b' * 64}"
        namespace["metadata"]["annotations"][PREFIX + f"approved-{name}-images"] = old_image
        candidate[0]["metadata"]["annotations"][PREFIX + f"approved-{name}-images"] = (
            new_image + "," + old_image
        )
        deployments.append(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "enterprise-doc-" + name,
                    "namespace": NS,
                    "uid": name + "-uid",
                    "resourceVersion": "1",
                },
                "spec": {
                    "replicas": 1,
                    "template": {
                        "metadata": {
                            "annotations": {PREFIX + "config-sha256": canonical_digest(config)}
                        },
                        "spec": {"containers": [{"name": name, "image": old_image}]},
                    },
                },
            }
        )
    desired = copy.deepcopy(deployments)
    for item in desired:
        item["spec"]["template"]["spec"]["containers"][0]["image"] = item["spec"]["template"][
            "spec"
        ]["containers"][0]["image"].replace("a" * 64, "b" * 64)
        item["spec"]["template"]["metadata"]["annotations"][PREFIX + "config-sha256"] = (
            canonical_digest(new_config)
        )
    return {
        "schema_version": 2,
        "operation": "release-test",
        "executor": "c" * 40,
        "namespace_uid": "namespace-uid",
        "original_revision": "20260924_0031",
        "original_prerequisites": original,
        "candidate_prerequisites": candidate,
        "deployments": deployments,
        "candidate_deployments": desired,
        "jobs": [],
        "secret": {
            "uid": "secret-uid",
            "old_key": "b2xk",
            "new_key": "bmV3",
            "other_data_sha256": canonical_digest({"DATABASE__URL": "unchanged"}),
        },
    }


class Boundary:
    def __init__(self, data: dict[str, Any]) -> None:
        self.items = copy.deepcopy(data["original_prerequisites"] + data["deployments"])
        self.items.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": "enterprise-doc-secrets",
                    "namespace": NS,
                    "uid": "secret-uid",
                    "resourceVersion": "1",
                },
                "data": {"MODEL__API_KEY": data["secret"]["old_key"], "DATABASE__URL": "unchanged"},
            }
        )
        self.writes: list[tuple[str, str]] = []
        self.jobs: list[dict[str, Any]] = []
        self.fail: str | None = None

    def __call__(self, args: list[str], payload: str | None, timeout: float) -> str:
        assert 0 < timeout <= 10
        if args[0] == "get":
            if args[1] in {"namespace", "secret", "configmap", "deployment"}:
                return json.dumps(next(i for i in self.items if i["metadata"]["name"] == args[2]))
            if args[1] == "deployments":
                items = [i for i in self.items if i["kind"] == "Deployment"]
            elif args[1] == "jobs":
                items = self.jobs
            elif args[1] in {"pods", "hpa,cronjobs,statefulsets,daemonsets"}:
                items = []
            else:
                items = [i for i in self.items if i["kind"] == "ConfigMap"]
            return json.dumps({"items": items})
        if args[0] == "patch":
            assert payload
            item = next(i for i in self.items if i["metadata"]["name"] == args[2])
            if self.fail == args[2]:
                raise TimeoutError("do-not-log-secret")
            for patch in json.loads(payload):
                dest: Any = item
                parts = patch["path"].strip("/").split("/")
                for part in parts[:-1]:
                    dest = dest[part.replace("~1", "/").replace("~0", "~")]
                key = parts[-1].replace("~1", "/").replace("~0", "~")
                if patch["op"] == "test":
                    assert dest[key] == patch["value"]
                elif patch["op"] == "remove":
                    del dest[key]
                else:
                    dest[key] = patch["value"]
            item["metadata"]["resourceVersion"] = str(int(item["metadata"]["resourceVersion"]) + 1)
            if item["kind"] == "Deployment":
                item["status"] = {
                    key: item["spec"]["replicas"]
                    for key in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
                }
            self.writes.append((args[1], args[2]))
            return json.dumps(item)
        if args[0] == "rollout":
            return "ready"
        raise AssertionError(args)


@pytest.mark.parametrize("api_pool_override", [False, True])
def test_release_restores_complete_bundle_after_partial_switch(api_pool_override: bool) -> None:
    from scripts.release_switch import ReleaseCluster, ReleasePlan

    data = release_data()
    if api_pool_override:
        data["api_database_pool_size"] = 4
        data["candidate_deployments"][0]["spec"]["template"]["spec"]["containers"][0]["env"] = [
            {"name": "DATABASE__POOL_SIZE", "value": "4"},
            {"name": "DATABASE__MAX_OVERFLOW", "value": "0"},
        ]
    boundary = Boundary(data)
    for item in boundary.items:
        if item["kind"] == "ConfigMap":
            item["data"] = copy.deepcopy(data["candidate_prerequisites"][1]["data"])
        if item["kind"] == "Secret":
            item["data"]["MODEL__API_KEY"] = data["secret"]["new_key"]
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api":
            item["spec"] = copy.deepcopy(data["candidate_deployments"][0]["spec"])
    cluster = ReleaseCluster(
        ReleasePlan(data), run=boundary, revision=lambda timeout: "20260924_0031", clock=lambda: 1.0
    )
    cluster.restore(100)
    assert boundary.writes[-1] == ("deployment", "enterprise-doc-web")
    for original in data["original_prerequisites"] + data["deployments"]:
        item = next(
            i for i in boundary.items if i["metadata"]["name"] == original["metadata"]["name"]
        )
        for key in ("data", "spec"):
            if key in original:
                assert item[key] == original[key]
    secret = next(i for i in boundary.items if i["kind"] == "Secret")
    assert secret["data"] == {
        "MODEL__API_KEY": data["secret"]["old_key"],
        "DATABASE__URL": "unchanged",
    }


def test_release_clears_temporary_fences_for_normal_deployment_validation() -> None:
    from scripts.maintenance_guard_cluster import validate_objects
    from scripts.release_switch import FENCE, ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)
    ReleaseCluster(
        ReleasePlan(data), run=boundary, revision=lambda timeout: "20260924_0031", clock=lambda: 1.0
    ).restore(100)
    assert all(FENCE not in i["metadata"].get("annotations", {}) for i in boundary.items)
    validate_objects(
        data["original_prerequisites"],
        [i for i in boundary.items if i["kind"] in {"ConfigMap", "Namespace"}],
    )


@pytest.mark.parametrize("pool_size", [1, 4])
@pytest.mark.parametrize("existing_override", [False, True])
def test_declared_api_pool_switch_and_restore_preserve_other_environment(
    pool_size: int, existing_override: bool
) -> None:
    from scripts.release_switch import ReleaseCluster, ReleasePlan

    data = release_data()
    data["api_database_pool_size"] = pool_size
    original = data["deployments"][0]["spec"]["template"]["spec"]["containers"][0]
    candidate = data["candidate_deployments"][0]["spec"]["template"]["spec"]["containers"][0]
    original["env"] = [
        {"name": "ROLE", "value": "api"},
        {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
    ]
    if existing_override:
        original["env"] += [
            {"name": "DATABASE__POOL_SIZE", "value": "1"},
            {"name": "DATABASE__MAX_OVERFLOW", "value": "0"},
        ]
    candidate["env"] = [
        *copy.deepcopy(original["env"][:2]),
        {"name": "DATABASE__POOL_SIZE", "value": str(pool_size)},
        {"name": "DATABASE__MAX_OVERFLOW", "value": "0"},
    ]
    frozen = copy.deepcopy(data)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20260924_0031",
        idle=lambda timeout: True,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)
    assert data == frozen
    for baseline in data["deployments"]:
        actual = next(
            i for i in boundary.items if i["metadata"]["name"] == baseline["metadata"]["name"]
        )
        assert actual["spec"] == baseline["spec"]


@pytest.mark.parametrize("pool_size", [None, True, False, "4", 4.0, 0, 5, -1])
def test_api_pool_declaration_rejects_invalid_bounds_and_types(pool_size: Any) -> None:
    from scripts.release_switch import ReleasePlan

    data = release_data()
    data["api_database_pool_size"] = pool_size
    with pytest.raises(ValueError, match="API pool override"):
        ReleasePlan(data)


@pytest.mark.parametrize(
    "change",
    [
        "undeclared",
        "missing",
        "mismatch",
        "overflow",
        "value_from",
        "duplicate",
        "baseline_duplicate",
        "unrelated_duplicate",
        "unrelated_value",
        "unrelated_order",
        "worker",
        "consumer",
        "web",
        "resources",
        "env_from",
    ],
)
def test_api_pool_override_cannot_hide_unreviewed_workload_changes(change: str) -> None:
    from scripts.release_switch import ReleasePlan

    data = release_data()
    data["api_database_pool_size"] = 4
    original = data["deployments"][0]["spec"]["template"]["spec"]["containers"][0]
    candidate = data["candidate_deployments"][0]["spec"]["template"]["spec"]["containers"][0]
    original["env"] = [{"name": "A", "value": "a"}, {"name": "B", "value": "b"}]
    candidate["env"] = [
        *copy.deepcopy(original["env"]),
        {"name": "DATABASE__POOL_SIZE", "value": "4"},
        {"name": "DATABASE__MAX_OVERFLOW", "value": "0"},
    ]
    if change == "undeclared":
        del data["api_database_pool_size"]
    elif change == "missing":
        candidate["env"].pop()
    elif change == "mismatch":
        candidate["env"][2]["value"] = "3"
    elif change == "overflow":
        candidate["env"][3]["value"] = "1"
    elif change == "value_from":
        candidate["env"][2]["valueFrom"] = {"secretKeyRef": {"name": "pool", "key": "size"}}
    elif change == "duplicate":
        candidate["env"].append(copy.deepcopy(candidate["env"][2]))
    elif change == "baseline_duplicate":
        original["env"].extend([{"name": "DATABASE__POOL_SIZE", "value": "1"}] * 2)
    elif change == "unrelated_duplicate":
        candidate["env"].append(copy.deepcopy(candidate["env"][0]))
    elif change == "unrelated_value":
        candidate["env"][0]["value"] = "changed"
    elif change == "unrelated_order":
        candidate["env"][:2] = reversed(candidate["env"][:2])
    elif change in {"worker", "consumer", "web"}:
        data["candidate_deployments"][NAMES.index(change)]["spec"]["template"]["spec"][
            "containers"
        ][0]["env"] = copy.deepcopy(candidate["env"][2:])
    elif change == "resources":
        candidate["resources"] = {"limits": {"memory": "2Gi"}}
    else:
        candidate["envFrom"] = [{"configMapRef": {"name": "other-config"}}]
    with pytest.raises(ValueError):
        ReleasePlan(data)


def test_interrupted_process_resumes_only_rollback_with_original_deadline(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Target, Tick
    from scripts.release_switch import Switch

    target = Target("switch", "a" * 40, "namespace", "b" * 64)
    tick = [Tick("boot", 1, 100)]
    controller = Switch(tmp_path / "state.json", clock=lambda: tick[0])
    controller.arm(target, timeout=60, recovery_budget=30)
    calls: list[str] = []

    def apply(deadline: float) -> None:
        calls.append("apply")
        assert controller.status()["phase"] == "applying"
        raise SystemExit("simulate-killed-process")

    with pytest.raises(SystemExit):
        controller.execute(target, apply=apply, restore=lambda deadline: calls.append("restore"))
    tick[0] = Tick("boot", 5, 104)
    result = controller.execute(
        target, apply=apply, restore=lambda deadline: calls.append("restore")
    )
    assert result["phase"] == "restored"
    assert result["deadline"] == 61
    assert calls == ["apply", "restore"]
    controller.execute(target, apply=apply, restore=lambda deadline: calls.append("again"))
    assert calls == ["apply", "restore"]


@pytest.mark.parametrize(
    "drift",
    [
        "revision",
        "database-key",
        "primary-key",
        "deployment",
        "config",
        "namespace",
        "job",
        "fence",
    ],
)
def test_drift_is_rejected_without_mutations(drift: str) -> None:
    from scripts.release_switch import FENCE, ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)
    revision = "20260924_0031"
    if drift == "revision":
        revision = "20260923_0027"
    elif drift.endswith("key"):
        secret = next(i for i in boundary.items if i["kind"] == "Secret")
        secret["data"]["DATABASE__URL" if drift == "database-key" else "MODEL__API_KEY"] = "drift"
    elif drift == "deployment":
        next(i for i in boundary.items if i["kind"] == "Deployment")["spec"]["template"]["spec"][
            "hostNetwork"
        ] = True
    elif drift == "config":
        next(i for i in boundary.items if i["kind"] == "ConfigMap")["data"]["UNAPPROVED"] = "true"
    elif drift == "namespace":
        boundary.items[0]["metadata"]["uid"] = "another-namespace"
    elif drift == "job":
        boundary.jobs = [
            {"metadata": {"name": "unreviewed", "uid": "job"}, "status": {"active": 1}}
        ]
    else:
        boundary.items[0]["metadata"]["annotations"][FENCE] = "other-operation:" + "a" * 32
    with pytest.raises(ValueError):
        ReleaseCluster(
            ReleasePlan(data), run=boundary, revision=lambda timeout: revision, clock=lambda: 1
        ).restore(100)
    assert not boundary.writes


@pytest.mark.parametrize(
    "change", ["revision", "config", "namespace", "image", "template", "secret", "failover"]
)
def test_plan_rejects_unreviewed_scope(change: str) -> None:
    from scripts.release_switch import ReleasePlan

    data = release_data()
    if change == "revision":
        data["original_revision"] = "20260923_0027"
    elif change == "config":
        data["candidate_prerequisites"][1]["data"]["DEMO__ENABLED"] = "true"
    elif change == "namespace":
        data["candidate_prerequisites"][0]["metadata"]["annotations"]["unapproved"] = "true"
    elif change == "image":
        data["candidate_deployments"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "latest"
        )
    elif change == "template":
        data["candidate_deployments"][0]["spec"]["template"]["spec"]["hostNetwork"] = True
    elif change == "secret":
        data["secret"]["new_key"] = "invalid"
    else:
        cfg = data["candidate_prerequisites"][1]["data"]
        cfg["PRESALES__AUTOMATIC_FAILOVER_ENABLED"] = "true"
        data["candidate_prerequisites"][0]["metadata"]["annotations"][
            PREFIX + "approved-config-sha256"
        ] = canonical_digest(cfg)
    with pytest.raises(ValueError):
        ReleasePlan(data)


def test_successful_switch_verifies_bundle_and_never_invokes_migrations_or_smoke() -> None:
    from scripts.release_switch import FENCE, ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20260924_0031",
        idle=lambda timeout: True,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    assert all(FENCE not in i["metadata"].get("annotations", {}) for i in boundary.items)
    assert {kind for kind, name in boundary.writes} == {
        "namespace",
        "configmap",
        "secret",
        "deployment",
    }


def test_busy_database_rejects_before_closing_web() -> None:
    from scripts.release_switch import ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20260924_0031",
        idle=lambda timeout: False,
        clock=lambda: 1,
    )
    with pytest.raises(ValueError, match="active business"):
        cluster.apply(100)
    assert not boundary.writes


def test_busy_database_at_execution_does_not_trigger_rollback(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Target, Tick
    from scripts.release_switch import ReleaseCluster, ReleasePlan, Switch

    data = release_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20260924_0031",
        idle=lambda timeout: False,
        clock=lambda: 1,
    )
    target = Target("switch", "a" * 40, "namespace", "b" * 64)
    switch = Switch(tmp_path / "state.json", clock=lambda: Tick("boot", 1, 1))
    switch.arm(target)
    result = switch.execute(target, apply=cluster.apply, restore=cluster.restore)
    assert result["phase"] == "blocked"
    assert result["reason"] == "preflight_rejected"
    assert not boundary.writes


def test_failed_backend_restore_keeps_web_closed() -> None:
    from scripts.release_switch import ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)

    def run(args: list[str], payload: str | None, timeout: float) -> str:
        if args[0] == "rollout":
            raise TimeoutError("private-error")
        return boundary(args, payload, timeout)

    with pytest.raises(TimeoutError):
        ReleaseCluster(
            ReleasePlan(data), run=run, revision=lambda timeout: "20260924_0031", clock=lambda: 1
        ).restore(100)
    web = next(i for i in boundary.items if i["metadata"]["name"] == "enterprise-doc-web")
    assert web["spec"]["replicas"] == 0


def test_late_forward_patch_cannot_override_restored_configuration() -> None:
    from scripts.release_switch import ReleaseCluster, ReleasePlan

    data = release_data()
    boundary = Boundary(data)
    delayed: list[tuple[list[str], str | None, float]] = []

    def run(args: list[str], payload: str | None, timeout: float) -> str:
        if not delayed and args[:3] == ["patch", "configmap", "enterprise-doc-config"]:
            delayed.append((args, payload, timeout))
            raise TimeoutError("acknowledgement-unknown")
        return boundary(args, payload, timeout)

    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=run,
        revision=lambda timeout: "20260924_0031",
        idle=lambda timeout: True,
        clock=lambda: 1,
    )
    with pytest.raises(TimeoutError):
        cluster.apply(100)
    cluster.restore(100)
    with pytest.raises(AssertionError):  # The API server rejects the old resourceVersion test.
        boundary(*delayed[0])
    cluster.verify(False, 100)


@pytest.mark.parametrize(
    "scenario", ["failed", "expired", "boot", "budget", "target", "recovery-failed"]
)
def test_switch_fences_failed_expired_and_mismatched_execution(
    tmp_path: Path, scenario: str
) -> None:
    from scripts.maintenance_guard import Target, Tick
    from scripts.release_switch import Switch

    target = Target("switch", "a" * 40, "namespace", "b" * 64)
    tick = [Tick("boot", 1, 100)]
    switch = Switch(tmp_path / "state.json", clock=lambda: tick[0])
    switch.arm(target, timeout=10, recovery_budget=10)
    calls: list[str] = []

    def apply(deadline: float) -> None:
        calls.append("apply")
        raise RuntimeError("never-log-this-secret")

    def restore(deadline: float) -> None:
        calls.append("restore")
        if scenario == "recovery-failed":
            raise RuntimeError("never-log-this-secret")

    if scenario == "boot":
        tick[0] = Tick("other-boot", 2, 101)
    if scenario == "budget":
        tick[0] = Tick("boot", 22, 121)
    if scenario == "expired":
        tick[0] = Tick("boot", 12, 111)
    if scenario == "target":
        with pytest.raises(ValueError):
            switch.execute(
                Target("other", "a" * 40, "namespace", "b" * 64), apply=apply, restore=restore
            )
        assert not calls
    else:
        result = switch.execute(target, apply=apply, restore=restore)
        assert result["phase"] == ("restored" if scenario == "failed" else "blocked")
        if scenario in {"boot", "budget"}:
            assert not calls
        if scenario == "expired":
            assert calls == []
    assert "never-log" not in (tmp_path / "state.json").read_text()
    with pytest.raises(FileExistsError):
        switch.arm(target)


def test_two_executors_cannot_acquire_same_window(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Target, Tick
    from scripts.release_switch import Switch, exclusive

    switch = Switch(tmp_path / "state.json", clock=lambda: Tick("boot", 1, 1))
    target = Target("switch", "a" * 40, "namespace", "b" * 64)
    switch.arm(target)
    with exclusive(tmp_path / "state.lock"), pytest.raises(OSError):
        switch.execute(
            target,
            apply=lambda deadline: pytest.fail("must not run"),
            restore=lambda deadline: pytest.fail("must not restore"),
        )


def test_cli_validates_exact_executor_bundle_without_exposing_credentials(tmp_path: Path) -> None:
    from scripts.release_switch import EXECUTOR_FILES

    repo = Path(__file__).resolve().parents[2]
    plan = release_data()
    plan["executor_sources"] = {
        name: hashlib.sha256((repo / "scripts" / name).read_bytes()).hexdigest()
        for name in EXECUTOR_FILES
    }
    path = tmp_path / "plan.json"

    def validate() -> subprocess.CompletedProcess[str]:
        path.write_bytes(json.dumps(plan).encode())
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "scripts.release_switch",
                "validate",
                "--plan",
                str(path),
                "--plan-sha256",
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )

    result = validate()
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "valid", "revision": "20260924_0031"}
    plan["executor_sources"]["release_switch.py"] = "0" * 64
    rejected = validate()
    assert rejected.returncode == 1 and "refused" in rejected.stderr
    assert not rejected.stdout
    assert plan["secret"]["old_key"] not in rejected.stderr


def test_business_arriving_during_close_restores_without_installing_candidate(
    tmp_path: Path,
) -> None:
    from scripts.maintenance_guard import Target, Tick
    from scripts.release_switch import ReleaseCluster, ReleasePlan, Switch

    data = release_data()
    boundary = Boundary(data)
    answers = iter((True, False))
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20260924_0031",
        clock=lambda: 1,
        idle=lambda timeout: next(answers),
    )
    target = Target("switch", "a" * 40, "namespace", "b" * 64)
    switch = Switch(tmp_path / "state.json", clock=lambda: Tick("boot", 1, 1))
    switch.arm(target)
    result = switch.execute(target, apply=cluster.apply, restore=cluster.restore)
    assert result["phase"] == "restored"
    cluster.verify(False, 100)


def test_database_check_is_readonly_bounded_and_recognizes_consumed_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.release_switch import database_idle

    monkeypatch.setenv("MAINTENANCE_GUARD_DATABASE_URL", "postgresql://user:secret@db.invalid/app")

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "BEGIN READ ONLY" in args[-1]
        assert "('consumed','released')" in args[-1]
        assert "product_usage_reservations" in args[-1]
        assert "expires_at > now()" in args[-1]
        assert "secret" not in " ".join(args)
        assert kwargs["timeout"] == 5
        assert "default_transaction_read_only=on" in kwargs["env"]["PGOPTIONS"]
        return subprocess.CompletedProcess(args, 0, stdout="t\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert database_idle(5)
