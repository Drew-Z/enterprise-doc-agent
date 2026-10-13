from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts.maintenance_guard import Guard, Target, Tick
from scripts.staging_maintenance import MaintenanceError, build_record, prepare, verify
from tests.deployment.test_maintenance_guard_cluster import plan_data

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "enterprise-doc-agent-staging"
SERVICES = ("api", "worker", "consumer", "web")


def fixtures(tmp_path: Path) -> dict[str, Path]:
    config = {
        "kind": "ConfigMap",
        "metadata": {"name": "enterprise-doc-config", "namespace": NAMESPACE},
        "data": {
            "EMBEDDING__PROVIDER": "openai_compatible",
            "EMBEDDING__BASE_URL": "https://vectors.example.test/v1",
            "EMBEDDING__MODEL_NAME": "existing-embedding",
            "EMBEDDING__VERSION": "3",
            "EMBEDDING__DIMENSION": "1024",
            "RETRIEVAL__REQUIRE_VECTOR_EVIDENCE": "true",
        },
    }
    deployments = [
        {
            "kind": "Deployment",
            "metadata": {"name": f"enterprise-doc-{name}", "namespace": NAMESPACE},
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": name, "image": f"ghcr.io/test/{name}@sha256:{'a' * 64}"}
                        ]
                    }
                },
            },
        }
        for name in SERVICES
    ]
    resources = [
        config,
        *deployments,
        {
            "kind": "Deployment",
            "metadata": {"name": "enterprise-doc-redis"},
            "spec": {"replicas": 1},
        },
    ]
    resources.extend(
        {"kind": "Job", "metadata": {"name": name}}
        for name in ("enterprise-doc-migrate", "enterprise-doc-embedding-rollout")
    )
    live = copy.deepcopy(deployments)
    for item in live:
        item["spec"]["replicas"] = 0
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("config", "deployments", "pods", "prepared", "verified")
    }
    paths.update(source=tmp_path / "candidate.yaml", workloads=tmp_path / "paused.yaml")
    paths["source"].write_text(yaml.safe_dump_all(resources), encoding="utf-8")
    paths["config"].write_text(json.dumps(config), encoding="utf-8")
    paths["deployments"].write_text(json.dumps({"kind": "List", "items": live}), encoding="utf-8")
    paths["pods"].write_text(json.dumps({"kind": "List", "items": []}), encoding="utf-8")
    return paths


def run_prepare(paths: dict[str, Path]) -> dict[str, object]:
    return prepare(
        paths["source"],
        paths["config"],
        paths["deployments"],
        paths["pods"],
        paths["workloads"],
        paths["prepared"],
    )


def test_prepare_and_verify_preserve_vector_and_only_apply_four_paused_apps(tmp_path: Path) -> None:
    paths = fixtures(tmp_path)
    source = paths["source"].read_bytes()
    result = run_prepare(paths)
    output = list(yaml.safe_load_all(paths["workloads"].read_text()))
    assert {item["metadata"]["name"] for item in output} == {
        f"enterprise-doc-{s}" for s in SERVICES
    }
    assert all(item["spec"]["replicas"] == 0 for item in output)
    assert paths["source"].read_bytes() == source
    checked = verify(
        paths["source"],
        paths["workloads"],
        paths["config"],
        paths["deployments"],
        paths["pods"],
        paths["verified"],
    )
    assert checked["vector_config_sha256"] == result["vector_config_sha256"]
    assert checked["status"] == "maintenance_verified"
    assert "vectors.example.test" not in paths["verified"].read_text()


@pytest.mark.parametrize(
    "key",
    [
        "EMBEDDING__VERSION",
        "EMBEDDING__MODEL_NAME",
        "EMBEDDING__BASE_URL",
        "EMBEDDING__DIMENSION",
        "EMBEDDING__QUERY_INSTRUCTION",
        "RETRIEVAL__REQUIRE_VECTOR_EVIDENCE",
    ],
)
def test_vector_drift_refuses_before_writing_workloads(tmp_path: Path, key: str) -> None:
    paths = fixtures(tmp_path)
    config = json.loads(paths["config"].read_text())
    config["data"][key] = "different-private-value"
    paths["config"].write_text(json.dumps(config))
    with pytest.raises(MaintenanceError, match="vector configuration") as exc:
        run_prepare(paths)
    assert "different-private-value" not in str(exc.value)
    assert not paths["workloads"].exists()


@pytest.mark.parametrize(
    "field", ["replicas", "readyReplicas", "availableReplicas", "updatedReplicas"]
)
def test_residual_deployment_replica_refuses(tmp_path: Path, field: str) -> None:
    paths = fixtures(tmp_path)
    live = json.loads(paths["deployments"].read_text())
    live["items"][0].setdefault("status", {})[field] = 1
    paths["deployments"].write_text(json.dumps(live))
    with pytest.raises(MaintenanceError, match="paused"):
        run_prepare(paths)


@pytest.mark.parametrize(
    "name",
    [
        "enterprise-doc-worker",
        "enterprise-doc-migrate",
        "enterprise-doc-embedding-rollout",
        "unknown-writer",
    ],
)
@pytest.mark.parametrize("phase", ["Pending", "Running", "Unknown"])
def test_active_namespace_pod_refuses_even_when_replica_count_is_zero(
    tmp_path: Path, name: str, phase: str
) -> None:
    paths = fixtures(tmp_path)
    paths["pods"].write_text(
        json.dumps(
            {
                "kind": "List",
                "items": [
                    {
                        "kind": "Pod",
                        "metadata": {"labels": {"app.kubernetes.io/name": name}},
                        "status": {"phase": phase},
                    }
                ],
            }
        )
    )
    with pytest.raises(MaintenanceError, match="active Pod"):
        run_prepare(paths)


def test_missing_or_duplicate_application_and_floating_image_refuse(tmp_path: Path) -> None:
    paths = fixtures(tmp_path)
    original = list(yaml.safe_load_all(paths["source"].read_text()))
    for variant in (original[:1] + original[2:], [*original, original[1]]):
        paths["source"].write_text(yaml.safe_dump_all(variant))
        with pytest.raises(MaintenanceError):
            run_prepare(paths)
    original[1]["spec"]["template"]["spec"]["containers"][0]["image"] = "example/api:latest"
    paths["source"].write_text(yaml.safe_dump_all(original))
    with pytest.raises(MaintenanceError, match="immutable"):
        run_prepare(paths)


def test_post_apply_verification_refuses_wrong_image_or_restarted_workload(tmp_path: Path) -> None:
    paths = fixtures(tmp_path)
    run_prepare(paths)
    live = json.loads(paths["deployments"].read_text())
    live["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
        f"other/api@sha256:{'b' * 64}"
    )
    paths["deployments"].write_text(json.dumps(live))
    with pytest.raises(MaintenanceError, match="image"):
        verify(
            paths["source"],
            paths["workloads"],
            paths["config"],
            paths["deployments"],
            paths["pods"],
            paths["verified"],
        )


def test_receipt_never_calls_maintenance_a_passed_release(tmp_path: Path) -> None:
    paths = fixtures(tmp_path)
    run_prepare(paths)
    verify(
        paths["source"],
        paths["workloads"],
        paths["config"],
        paths["deployments"],
        paths["pods"],
        paths["verified"],
    )
    outcomes = dict.fromkeys(
        ("prerequisites", "maintenance_prepare", "migration", "workloads", "maintenance_verify"),
        "success",
    )
    outcomes.update(
        dict.fromkeys(
            (
                "rollout",
                "embedding_rollout",
                "cluster_smoke",
                "authenticated_smoke",
                "governance_smoke",
                "browser_identity",
            ),
            "skipped",
        )
    )
    report = build_record(
        paths["prepared"], paths["verified"], outcomes, "c" * 40, tmp_path / "receipt.json"
    )
    assert report["status"] == "blocked_external"
    assert report["maintenance_status"] == "installed_paused"
    assert report["production_capacity_approved"] is False
    outcomes["embedding_rollout"] = "success"
    failed = build_record(
        paths["prepared"], paths["verified"], outcomes, "c" * 40, tmp_path / "failed.json"
    )
    assert failed["status"] == "failed"


def test_missing_receipts_and_failed_migration_are_written_as_failures(tmp_path: Path) -> None:
    record = build_record(
        tmp_path / "missing",
        tmp_path / "absent",
        {"migration": "failure"},
        "c" * 40,
        tmp_path / "record.json",
    )
    assert record["status"] == "failed"
    assert json.loads((tmp_path / "record.json").read_text())["outcomes"]["migration"] == "failure"


@pytest.mark.parametrize(
    ("mode", "smoke", "profile", "allowed"),
    [
        ("maintenance", "false", "single-node-4c4g", True),
        ("maintenance", "true", "single-node-4c4g", False),
        ("maintenance", "false", "tiny-single-node", False),
        ("unknown", "false", "single-node-4c4g", False),
        ("standard", "true", "single-node-4c4g", True),
    ],
)
def test_actual_dispatch_gate_rejects_conflicting_modes(
    tmp_path: Path, mode: str, smoke: str, profile: str, allowed: bool
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yml").read_text())
    step = next(
        item
        for item in workflow["jobs"]["deploy"]["steps"]
        if item.get("name") == "Validate staging dispatch gate"
    )
    assert step["env"]["DEPLOYMENT_MODE"] == "${{ vars.STAGING_DEPLOYMENT_MODE || 'standard' }}"
    assert step["env"]["RUN_SMOKE"] == "${{ inputs.run_smoke }}"
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash
    env = {
        **os.environ,
        "CONTROL_PLANE_APPROVED": "true",
        "DEPLOYMENT_MODE": mode,
        "RUN_SMOKE": smoke,
        "DEPLOYMENT_PROFILE": profile,
    }
    env.pop("BASH_ENV", None)
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", step["run"]], env=env, capture_output=True, timeout=10
    )
    assert (result.returncode == 0) is allowed


def test_workflow_gates_all_calling_steps_and_records_maintenance_separately() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yml").read_text())
    steps = {s.get("id"): s for s in workflow["jobs"]["deploy"]["steps"] if "id" in s}
    for name in ("embedding_rollout", "rollout", "browser_identity"):
        assert "vars.STAGING_DEPLOYMENT_MODE != 'maintenance'" in steps[name]["if"]
    for name in ("maintenance_prepare", "maintenance_verify"):
        assert steps[name]["if"] == "vars.STAGING_DEPLOYMENT_MODE == 'maintenance'"
    collect = next(
        s
        for s in workflow["jobs"]["deploy"]["steps"]
        if s.get("name") == "Collect sanitized release evidence"
    )
    assert "scripts/staging_maintenance.py record" in collect["run"]


def test_actual_migration_rejects_missing_guard_before_any_cluster_command(tmp_path: Path) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yml").read_text())
    step = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("id") == "migration")
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash
    env = {
        **os.environ,
        "DEPLOYMENT_MODE": "maintenance",
        "DEPLOYMENT_PROFILE": "single-node-4c4g",
        "RUNNER_TEMP": tmp_path.as_posix(),
    }
    env.pop("BASH_ENV", None)
    command = (
        'kubectl() { printf "%s\\n" "$*" >> "$RUNNER_TEMP/commands"; return 71; }\n' + step["run"]
    )
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not (tmp_path / "commands").exists()


@pytest.mark.parametrize("fail_migration", [False, True])
def test_actual_maintenance_steps_run_real_cli_with_only_cluster_boundary_replaced(
    tmp_path: Path, fail_migration: bool
) -> None:
    paths = fixtures(tmp_path)
    (tmp_path / "staging.yaml").write_bytes(paths["source"].read_bytes())
    guard_env = workflow_guard(tmp_path)
    (tmp_path / "staging-migration.yaml").write_text(
        "kind: Job\nmetadata:\n  name: enterprise-doc-migrate\n"
    )
    recorder = tmp_path / "kubectl_fixture.py"
    recorder.write_text(
        "import sys,os,json\nfrom pathlib import Path\nimport yaml\n"
        "root=Path(os.environ['RUNNER_TEMP']); args=sys.argv[1:]\n"
        "with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\\n')\n"
        "if args[:2]==['-n','enterprise-doc-agent-staging']:args=args[2:]\n"
        "if args[:2]==['get','configmap']:print((root/'config.json').read_text())\n"
        "elif args[:2]==['get','deployments']:print((root/'deployments.json').read_text())\n"
        "elif args[:2]==['get','pods']:print((root/'pods.json').read_text())\n"
        "elif args[:2]==['get','job']:sys.exit(1)\n"
        "elif args[:2]==['get','job/enterprise-doc-migrate']:\n"
        " print('True' if 'Complete' in args[-1] else '')\n"
        "elif args[0] in ('scale','delete'):pass\n"
        "elif args[0]=='apply':\n"
        " path=Path(args[-1])\n"
        " if '--dry-run=server' not in args:\n"
        "  if 'migration' in path.name and os.environ['FAIL_MIGRATION']=='true':sys.exit(29)\n"
        "  if 'workloads' in path.name:\n"
        "   items=list(yaml.safe_load_all(path.read_text()))\n"
        "   (root/'deployments.json').write_text(json.dumps({'items':items}))\n"
        "else:sys.exit(97)\n",
        encoding="utf-8",
    )
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yml").read_text())
    by_id = {step.get("id"): step for step in workflow["jobs"]["deploy"]["steps"] if "id" in step}
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash
    env = {
        **os.environ,
        "RUNNER_TEMP": tmp_path.as_posix(),
        "RUNNER_PYTHON": Path(sys.executable).as_posix(),
        "KUBECTL_FIXTURE": recorder.as_posix(),
        "DEPLOYMENT_PROFILE": "single-node-4c4g",
        "DEPLOYMENT_MODE": "maintenance",
        "FAIL_MIGRATION": str(fail_migration).lower(),
        **guard_env,
    }
    env.pop("BASH_ENV", None)
    calls = []
    phases = [
        step["id"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("id") in {"maintenance_prepare", "migration", "workloads", "maintenance_verify"}
    ]
    assert phases == ["maintenance_prepare", "migration", "workloads", "maintenance_verify"]
    for phase in phases:
        command = (
            'fixture_python() { "$PYTHON_REAL" "$PYTHON_CLOCK_BOUNDARY" "$@"; }\n'
            'kubectl() { "$PYTHON_REAL" "$KUBECTL_FIXTURE" "$@"; }\n' + by_id[phase]["run"]
        )
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        calls.append((phase, result.returncode))
        if result.returncode:
            break
    actual = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert not any("embedding" in " ".join(call) or "smoke" in " ".join(call) for call in actual)
    assert Guard(tmp_path / "guard.db").status()["phase"] == "migration_claimed"
    if fail_migration:
        assert calls == [("maintenance_prepare", 0), ("migration", 29)]
        assert not (tmp_path / "staging-maintenance-verified.json").exists()
    else:
        assert calls == [
            (phase, 0)
            for phase in ("maintenance_prepare", "migration", "workloads", "maintenance_verify")
        ]
        receipt = json.loads((tmp_path / "staging-maintenance-verified.json").read_text())
        assert receipt["target_replicas"] == 0
        applied = [
            call[-1] for call in actual if call[0] == "apply" and "--dry-run=server" not in call
        ]
        assert applied == [
            (tmp_path / "staging-migration.yaml").as_posix(),
            (tmp_path / "staging-workloads.yaml").as_posix(),
        ]


def workflow_guard(tmp_path: Path) -> dict[str, str]:
    from scripts.maintenance_guard_cluster import CONFIG_ADDITIONS, canonical_digest

    plan = plan_data()
    config = json.loads((tmp_path / "config.json").read_text())
    plan["original_prerequisites"][1]["data"] = config["data"]
    plan["candidate_prerequisites"][1]["data"] = config["data"] | CONFIG_ADDITIONS
    documents = list(yaml.safe_load_all((tmp_path / "staging.yaml").read_text()))
    documents[0] = plan["candidate_prerequisites"][1]
    documents.insert(0, plan["candidate_prerequisites"][0])
    (tmp_path / "staging.yaml").write_text(yaml.safe_dump_all(documents))
    (tmp_path / "config.json").write_text(json.dumps(plan["candidate_prerequisites"][1]))
    plan["candidate_sha256"] = canonical_digest(
        list(yaml.safe_load_all((tmp_path / "staging.yaml").read_text()))
    )
    path = tmp_path / "guard-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    guard = Guard(tmp_path / "guard.db", clock=lambda: Tick("workflow-boot", 10, 1000))
    guard.arm(Target(plan["operation"], plan["executor"], plan["namespace_uid"], digest))
    guard.poll()
    clock = tmp_path / "python_clock_boundary.py"
    clock.write_text(
        "import sys,runpy\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from scripts import maintenance_guard as guard\n"
        "guard.host_clock=lambda:guard.Tick('workflow-boot',10,1000)\n"
        "sys.argv=sys.argv[1:]\n"
        "if Path(sys.argv[0]).name=='maintenance_guard.py':guard.main()\n"
        "else:runpy.run_path(sys.argv[0],run_name='__main__')\n",
        encoding="utf-8",
    )
    return {
        "RUNNER_PYTHON": "fixture_python",
        "PYTHON_REAL": Path(sys.executable).as_posix(),
        "PYTHON_CLOCK_BOUNDARY": clock.as_posix(),
        "MAINTENANCE_GUARD_STATE": guard.path.as_posix(),
        "MAINTENANCE_GUARD_PLAN": path.as_posix(),
        "MAINTENANCE_GUARD_PLAN_SHA256": digest,
        "MAINTENANCE_OPERATION": plan["operation"],
        "EXPECTED_NAMESPACE_UID": plan["namespace_uid"],
        "GITHUB_SHA": plan["executor"],
    }


@pytest.mark.parametrize(
    "invalid",
    [
        "wrong-executor",
        "wrong-namespace",
        "wrong-operation",
        "plan-tampered",
        "candidate-tampered",
        "missing-state",
        "stale",
        "expired",
        "recovering",
        "claimed",
    ],
)
def test_actual_migration_cli_fences_invalid_or_late_workflow(tmp_path: Path, invalid: str) -> None:
    paths = fixtures(tmp_path)
    (tmp_path / "staging.yaml").write_bytes(paths["source"].read_bytes())
    values = workflow_guard(tmp_path)
    if invalid.startswith("wrong-"):
        key = {
            "wrong-executor": "GITHUB_SHA",
            "wrong-namespace": "EXPECTED_NAMESPACE_UID",
            "wrong-operation": "MAINTENANCE_OPERATION",
        }[invalid]
        values[key] = "c" * 40 if invalid == "wrong-executor" else "other"
    elif invalid == "plan-tampered":
        Path(values["MAINTENANCE_GUARD_PLAN"]).write_text("{}")
    elif invalid == "candidate-tampered":
        (tmp_path / "staging.yaml").write_text("kind: Unexpected\n")
    elif invalid == "missing-state":
        values["MAINTENANCE_GUARD_STATE"] = (tmp_path / "absent.db").as_posix()
    elif invalid in {"stale", "expired"}:
        clock_file = Path(values["PYTHON_CLOCK_BOUNDARY"])
        clock_file.write_text(
            clock_file.read_text().replace(
                "Tick('workflow-boot',10,1000)",
                f"Tick('workflow-boot',{21 if invalid == 'stale' else 1510},1000)",
            )
        )
    elif invalid == "recovering":
        Guard(tmp_path / "guard.db", clock=lambda: Tick("workflow-boot", 1510, 2500)).poll()
    else:
        Guard(tmp_path / "guard.db", clock=lambda: Tick("workflow-boot", 10, 1000)).claim(
            Target(
                values["MAINTENANCE_OPERATION"],
                values["GITHUB_SHA"],
                values["EXPECTED_NAMESPACE_UID"],
                values["MAINTENANCE_GUARD_PLAN_SHA256"],
            )
        )
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-staging.yml").read_text())
    step = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("id") == "migration")
    for key, value in {
        "DEPLOYMENT_MODE": "${{ vars.STAGING_DEPLOYMENT_MODE || 'standard' }}",
        "MAINTENANCE_GUARD_STATE": "${{ vars.STAGING_MAINTENANCE_GUARD_STATE }}",
        "MAINTENANCE_GUARD_PLAN": "${{ vars.STAGING_MAINTENANCE_GUARD_PLAN }}",
        "MAINTENANCE_GUARD_PLAN_SHA256": "${{ vars.STAGING_MAINTENANCE_GUARD_PLAN_SHA256 }}",
        "MAINTENANCE_OPERATION": "${{ vars.STAGING_MAINTENANCE_OPERATION }}",
        "EXPECTED_NAMESPACE_UID": "${{ vars.STAGING_NAMESPACE_UID }}",
    }.items():
        assert step["env"][key] == value
    env = {
        **os.environ,
        **values,
        "RUNNER_TEMP": tmp_path.as_posix(),
        "DEPLOYMENT_MODE": "maintenance",
        "DEPLOYMENT_PROFILE": "single-node-4c4g",
    }
    env.pop("BASH_ENV", None)
    command = (
        'fixture_python() { "$PYTHON_REAL" "$PYTHON_CLOCK_BOUNDARY" "$@"; }\n'
        'kubectl() { printf "%s\\n" "$*" >> "$RUNNER_TEMP/commands"; return 71; }\n' + step["run"]
    )
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not (tmp_path / "commands").exists()
    assert b"Traceback" not in result.stderr
