from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def execute_restore(
    tmp_path: Path,
    *,
    outcomes: dict[str, str] | None = None,
    profile: str = "single-node-4c4g",
    manifest_exists: bool = True,
    fail_call_at: int = 0,
) -> tuple[int, list[str]]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    )
    step = next(
        item
        for item in workflow["jobs"]["deploy"]["steps"]
        if item.get("name") == "Restore tiny workloads after deployment attempt"
    )
    values = {"prerequisites": "success", "migration": "success", "workloads": "success"}
    values.update(outcomes or {})
    if manifest_exists:
        (tmp_path / "staging-workloads.yaml").write_text("fixture", encoding="utf-8")
    calls = tmp_path / "calls.txt"
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash is not None and Path(bash).is_file(), "The workflow behavior test requires Bash"
    env = dict(os.environ)
    env.pop("BASH_ENV", None)
    env.update(
        {
            "RUNNER_TEMP": tmp_path.as_posix(),
            "KUBECTL_CALLS": calls.as_posix(),
            "DEPLOYMENT_PROFILE": profile,
            "FAIL_CALL_AT": str(fail_call_at),
        }
    )
    # Bind actual workflow environment expressions, not an invented decision function.
    for name, outcome in values.items():
        variable = name.upper() + "_OUTCOME"
        assert step["env"][variable] == "${{ steps." + name + ".outcome }}"
        env[variable] = outcome
    # Execute the checked-in run body; replace only the cluster process boundary.
    script = """
KUBECTL_COUNT=0
kubectl() {
  KUBECTL_COUNT=$((KUBECTL_COUNT + 1))
  printf '%s\n' "$*" >> "$KUBECTL_CALLS"
  if test "$KUBECTL_COUNT" = "$FAIL_CALL_AT"; then return 29; fi
}
""" + step["run"]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result.returncode, recorded


def test_failed_migration_never_applies_candidate(tmp_path: Path) -> None:
    exit_code, calls = execute_restore(tmp_path, outcomes={"migration": "failure"})
    assert exit_code == 0
    assert calls == []


@pytest.mark.parametrize("step", ["prerequisites", "migration", "workloads"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "", "unknown"])
def test_unproven_stage_performs_no_cluster_command(
    tmp_path: Path, step: str, outcome: str
) -> None:
    exit_code, calls = execute_restore(tmp_path, outcomes={step: outcome})
    assert exit_code == 0
    assert calls == []


@pytest.mark.parametrize(
    ("profile", "worker_timeout"), [("single-node-4c4g", 1800), ("tiny-single-node", 600)]
)
def test_success_resumes_only_applied_candidate_and_checks_all_workloads(
    tmp_path: Path, profile: str, worker_timeout: int
) -> None:
    exit_code, calls = execute_restore(tmp_path, profile=profile)
    assert exit_code == 0
    assert calls == [
        f"apply -f {tmp_path.as_posix()}/staging-workloads.yaml",
        "-n enterprise-doc-agent-staging rollout status "
        "deployment/enterprise-doc-api --timeout=600s",
        "-n enterprise-doc-agent-staging rollout status "
        f"deployment/enterprise-doc-worker --timeout={worker_timeout}s",
        "-n enterprise-doc-agent-staging rollout status "
        "deployment/enterprise-doc-consumer --timeout=600s",
        "-n enterprise-doc-agent-staging rollout status "
        "deployment/enterprise-doc-web --timeout=600s",
    ]


@pytest.mark.parametrize("profile", ["staging", "single-node-4c8g", ""])
def test_other_profiles_do_not_resume_workloads(tmp_path: Path, profile: str) -> None:
    assert execute_restore(tmp_path, profile=profile) == (0, [])


def test_missing_manifest_performs_no_cluster_command(tmp_path: Path) -> None:
    assert execute_restore(tmp_path, manifest_exists=False) == (0, [])


@pytest.mark.parametrize("failure_at", [1, 3])
def test_cluster_failure_propagates_without_later_commands(tmp_path: Path, failure_at: int) -> None:
    exit_code, calls = execute_restore(tmp_path, fail_call_at=failure_at)
    assert exit_code == 29
    assert len(calls) == failure_at
