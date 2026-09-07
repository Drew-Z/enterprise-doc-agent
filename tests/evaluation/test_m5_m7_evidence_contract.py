from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
FORMAL_MANIFEST_PATTERNS = {
    "M5": re.compile(r"^evidence/m5/\d{8}-\d{6}-m5-observability-eval-load\.json$"),
    "M6": re.compile(r"^evidence/m6/\d{8}-\d{6}-m6-cicd-kubernetes\.json$"),
    "M7": re.compile(r"^evidence/m7/\d{8}-\d{6}-m7-local-model-routing\.json$"),
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _repo_path(relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    assert not pure.is_absolute()
    assert ".." not in pure.parts
    path = ROOT.joinpath(*pure.parts)
    assert path.is_file(), relative_path
    return path


def _git_blob(commit_sha: str, relative_path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit_sha}:{relative_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def test_m5_m6_m7_formal_manifests_are_indexed_and_hashed() -> None:
    index = _load(ROOT / "evidence/index.json")
    assert index["schema_version"] == 1
    entries = index.get("evidence")
    assert isinstance(entries, list)
    by_milestone = {str(item["milestone"]): item for item in entries}
    for milestone in ("M5", "M6", "M7"):
        entry = by_milestone[milestone]
        assert entry["status"] == "blocked_external"
        assert FORMAL_MANIFEST_PATTERNS[milestone].fullmatch(str(entry["manifest"]))
        manifest_relative_path = str(entry["manifest"])
        manifest_path = _repo_path(manifest_relative_path)
        manifest = _load(manifest_path)
        assert manifest["status"] == "blocked_external"
        assert manifest["working_tree_dirty"] is False
        reviewed_commit = manifest["reviewed_commit"]
        evidence_commit = manifest["evidence_commit"]
        manifest_commit = manifest["manifest_commit"]
        assert isinstance(reviewed_commit, str) and SHA_PATTERN.fullmatch(reviewed_commit)
        assert isinstance(evidence_commit, str) and SHA_PATTERN.fullmatch(evidence_commit)
        assert isinstance(manifest_commit, str) and SHA_PATTERN.fullmatch(manifest_commit)
        assert reviewed_commit != evidence_commit
        assert manifest_commit not in {reviewed_commit, evidence_commit}
        assert manifest["commit_sha"] == reviewed_commit
        assert entry["manifest_commit"] == manifest_commit
        for commit_sha in (reviewed_commit, evidence_commit):
            subprocess.run(
                ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["git", "cat-file", "-e", f"{manifest_commit}^{{commit}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "cat-file", "-e", f"{manifest_commit}:{manifest_relative_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        required_fields = {
            "evidence_id",
            "milestone",
            "requirement_ids",
            "status",
            "command_or_procedure",
            "environment",
            "commit_sha",
            "image_digest",
            "started_at",
            "completed_at",
            "result_summary",
            "artifacts",
            "limitations",
            "owner",
            "reviewed_commit",
            "evidence_commit",
            "manifest_commit",
            "working_tree_dirty",
            "blocking_reason",
            "prerequisites",
        }
        assert required_fields <= set(manifest)
        started = datetime.fromisoformat(str(manifest["started_at"]))
        completed = datetime.fromisoformat(str(manifest["completed_at"]))
        assert started.tzinfo is not None
        assert completed >= started
        limitations = " ".join(str(item) for item in manifest["limitations"])
        assert "not" in limitations.lower()
        assert manifest["blocking_reason"]
        assert manifest["prerequisites"]
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list) and artifacts
        for artifact in artifacts:
            relative_path = str(artifact["path"])
            path = _repo_path(relative_path)
            assert path.stat().st_size > 0
            artifact_commit = str(artifact["commit_sha"])
            assert artifact_commit in {reviewed_commit, evidence_commit}
            assert (
                artifact["sha256"]
                == hashlib.sha256(_git_blob(artifact_commit, relative_path)).hexdigest()
            )


def test_working_tree_captures_are_separate_from_formal_evidence() -> None:
    index = _load(ROOT / "evidence/index.json")
    captures = index["working_tree_captures"]
    assert isinstance(captures, list)
    assert {item["milestone"] for item in captures} == {"M4", "M5", "M6", "M7"}
    for item in captures:
        assert item["status"] == "unreviewed"
        capture = _load(ROOT / str(item["capture"]))
        assert capture["status"] == "working-tree"
        assert capture["working_tree_dirty"] is True


def test_staging_trial_setup_failure_does_not_replace_provider_quality() -> None:
    index = _load(ROOT / "evidence/index.json")
    failure_path = "evidence/m5/20260906-staging-rag-trial-33976542098-setup-failure.json"
    for entry in index["evidence"]:
        if entry["milestone"] not in {"M5", "M7"}:
            continue
        assert entry["status"] == "blocked_external"
        assert entry["latest_staging_rag_trial_setup_failure"] == failure_path
        assert entry["latest_real_provider_quality"].endswith("v0.1.30-full-40.json")
        assert entry["latest_repeatability_attempt"].endswith("repeat-3-full-40.json")

    failure = _load(_repo_path(failure_path))
    assert failure["status"] == "failed"
    assert failure["failure_class"] == "evaluator_dependency_setup_timeout"
    assert failure["workflow"]["run_id"] == 33976542098
    assert failure["workflow"]["run_attempt"] == 1
    assert failure["workflow"]["conclusion"] == "cancelled"
    assert failure["execution"]["evaluation_step_conclusion"] == "skipped"
    assert failure["execution"]["submitted_case_count"] == 0
    assert failure["execution"]["report_produced"] is False
    assert failure["execution"]["artifact_count"] == 0
    assert failure["dataset"]["selected_case_count"] == 12
    assert failure["dataset"]["total_case_count"] == 40
    assert failure["evaluator_commit_sha"] != failure["staging_release"]["commit_sha"]
    assert "report_payload_sha256" not in failure
    assert "aggregate" not in failure


def test_staging_runtime_preparation_is_not_provider_quality_evidence() -> None:
    index = _load(ROOT / "evidence/index.json")
    preparation_path = "evidence/m5/20260907-staging-rag-evaluator-runtime-preparation.json"
    for entry in index["evidence"]:
        if entry["milestone"] not in {"M5", "M7"}:
            continue
        assert entry["status"] == "blocked_external"
        assert entry["latest_staging_rag_runtime_preparation"] == preparation_path
        assert entry["latest_real_provider_quality"].endswith("v0.1.30-full-40.json")
        assert entry["latest_repeatability_attempt"].endswith("repeat-3-full-40.json")

    preparation = _load(_repo_path(preparation_path))
    assert preparation["status"] == "prepared_offline"
    assert preparation["record_type"] == "evaluator-runtime-preparation"
    assert preparation["runtime"]["installed_package_count"] == 109
    assert preparation["runtime"]["development_tools_absent"] is True
    assert preparation["runtime"]["environment_path"] == (
        "/home/gha-staging/enterprise-doc-agent-evaluator-runtime/.venv"
    )
    assert preparation["preparation"]["fresh_registry_cache_complete"] is False
    verification = preparation["verification"]
    assert verification["workspace_rebuild"]["package_count"] == 4
    assert verification["workspace_rebuild"]["status"] == "passed"
    assert verification["frozen_offline_sync_check"] == "would_make_no_changes"
    assert verification["external_model_calls"] == 0
    assert verification["submitted_case_count"] == 0
    assert verification["real_quality_report_produced"] is False
    assert [
        (item["selection"], item["selected_case_count"]) for item in verification["selections"]
    ] == [
        ("trial", 12),
        ("full", 40),
    ]
    assert all(item["payload_checksum_valid"] for item in verification["selections"])
    assert "report_payload_sha256" not in preparation
    assert "aggregate" not in preparation

    lock_bytes = _git_blob(preparation["provenance"]["runner_checkout_sha"], "uv.lock")
    assert hashlib.sha256(lock_bytes).hexdigest() == preparation["lockfile"]["sha256"]
    locked_wheels = {
        PurePosixPath(urlsplit(wheel["url"]).path).name: wheel
        for package in tomllib.loads(lock_bytes.decode("utf-8"))["package"]
        for wheel in package.get("wheels", [])
    }
    wheels = preparation["preparation"]["verified_wheels"]
    assert len(wheels) == 5
    assert sum(wheel["size_bytes"] for wheel in wheels) == 35343009
    for wheel in wheels:
        locked = locked_wheels[wheel["filename"]]
        assert urlsplit(locked["url"]).hostname == "files.pythonhosted.org"
        assert locked["hash"] == "sha256:" + wheel["sha256"]
        assert locked["size"] == wheel["size_bytes"]


def test_staging_trial_checkout_failure_is_not_runtime_or_model_failure() -> None:
    index = _load(ROOT / "evidence/index.json")
    failure_path = "evidence/m5/20260908-staging-rag-trial-34143634860-checkout-failure.json"
    for entry in index["evidence"]:
        if entry["milestone"] not in {"M5", "M7"}:
            continue
        assert entry["status"] == "blocked_external"
        assert entry["latest_staging_rag_trial_checkout_failure"] == failure_path
        assert entry["latest_real_provider_quality"].endswith("v0.1.30-full-40.json")
        assert entry["latest_repeatability_attempt"].endswith("repeat-3-full-40.json")

    failure = _load(_repo_path(failure_path))
    assert failure["status"] == "failed"
    assert failure["failure_class"] == "evaluator_checkout_network_failure"
    assert failure["workflow"]["run_id"] == 34143634860
    assert failure["workflow"]["run_attempt"] == 1
    assert failure["workflow"]["conclusion"] == "failure"
    assert failure["workflow"]["scope"] == "trial"
    assert failure["evaluator_commit_sha"] == "c1e2ec7a6bb80da8fc68dc090d756fede8e5b716"
    execution = failure["execution"]
    assert execution["checkout_step_conclusion"] == "failure"
    assert execution["git_exit_code"] == 128
    assert execution["dependency_step_conclusion"] == "skipped"
    assert execution["evaluation_step_conclusion"] == "skipped"
    assert execution["submitted_case_count"] == 0
    assert execution["report_produced"] is False
    assert execution["artifact_count"] == 0
    assert execution["new_token_authenticated_by_this_run"] is False
    assert failure["preflight"]["loopback_session_authenticated"] is True
    assert failure["preflight"]["frozen_offline_sync_check"] == "would_make_no_changes"
    assert failure["preflight"]["installed_package_count"] == 109
    assert failure["dataset"]["selected_case_count"] == 12
    assert failure["dataset"]["total_case_count"] == 40
    assert failure["runtime_after_run"]["persistent_python_present"] is True
    assert "report_payload_sha256" not in failure
    assert "aggregate" not in failure


def test_manual_gates_have_complete_state_records() -> None:
    index = _load(ROOT / "evidence/index.json")
    paths = index["manual_gates"]
    assert isinstance(paths, list) and paths
    gate_ids: set[str] = set()
    required = {
        "gate_id",
        "requirement",
        "owner",
        "blocking_reason",
        "prerequisites",
        "required_evidence",
        "state",
        "status",
        "review_date",
    }
    for relative in paths:
        gate = _load(ROOT / str(relative))
        assert required <= set(gate)
        gate_id = str(gate["gate_id"])
        assert gate_id not in gate_ids
        gate_ids.add(gate_id)
        date.fromisoformat(str(gate["review_date"]))
        assert gate["required_evidence"]
        match gate["state"]:
            case "open":
                assert gate["status"] == "blocked_external"
                assert gate["blocking_reason"]
                assert gate["prerequisites"]
            case "closed":
                assert gate["status"] == "passed"
                assert gate["blocking_reason"] is None
                completed_evidence = gate.get("completed_evidence")
                assert isinstance(completed_evidence, list) and completed_evidence
                for completed_relative in completed_evidence:
                    _repo_path(str(completed_relative))
            case state:
                raise AssertionError(f"unsupported manual gate state: {state!r}")

    for milestone in ("M5", "M6", "M7"):
        manifest = _load(
            ROOT
            / str(
                next(
                    item["manifest"] for item in index["evidence"] if item["milestone"] == milestone
                )
            )
        )
        assert set(manifest["manual_gates"]) <= set(paths)
