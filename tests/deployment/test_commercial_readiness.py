import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.check_commercial_readiness import REQUIRED_CHECKS, check
from tests.deployment.test_validate_recovery_capacity_evidence import (
    _passed_capacity,
    _passed_recovery,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_commercial_readiness.py"
SHA = "a" * 40
NOW = datetime(2026, 9, 24, 8, tzinfo=UTC)
TARGET = {"commit_sha": SHA, "environment": "staging", "profile": "single-node-4c4g"}


def run_check(root, manifest="readiness.json", scope="invited"):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--root",
            str(root),
            "--manifest",
            manifest,
            "--commit",
            SHA,
            "--environment",
            "staging",
            "--profile",
            "single-node-4c4g",
            "--scope",
            scope,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_missing_evidence_cannot_produce_a_release_pass(tmp_path):
    result = run_check(tmp_path)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "blocked"
    assert report["issues"] == [{"gate": "manifest", "code": "evidence_file_unavailable"}]


def write_ref(root, path, data):
    target = root / path
    target.write_text(json.dumps(data), encoding="utf-8")
    return {"path": path, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def evidence_set(root, scope="invited"):
    rows, reports = [], {}
    for gate_id, checks in REQUIRED_CHECKS.items():
        if scope == "invited" and gate_id in {"payments", "self_service"}:
            continue
        raw = {
            **TARGET,
            "status": "passed",
            "completed_at": (NOW - timedelta(minutes=30)).isoformat(),
        }
        report = {
            **TARGET,
            "schema_version": 1,
            "gate_id": gate_id,
            "status": "passed",
            "completed_at": (NOW - timedelta(minutes=10)).isoformat(),
            "executed_by": "fixture-operator",
            "review": {
                "status": "approved",
                "reviewed_by": "fixture-reviewer",
                "reviewed_at": NOW.isoformat(),
            },
            "checks": dict.fromkeys(checks, True),
            "artifacts": [write_ref(root, f"{gate_id}-raw.json", raw)],
        }
        if gate_id in {"capacity", "recovery"}:
            measurement = (_passed_capacity if gate_id == "capacity" else _passed_recovery)(root)
            measurement["environment"].update(name=TARGET["environment"], profile=TARGET["profile"])
            measurement["completed_at"] = (NOW - timedelta(minutes=30)).isoformat()
            if gate_id == "recovery":
                measurement["review"]["reviewed_at"] = (NOW - timedelta(minutes=20)).isoformat()
            else:
                measurement["workload"]["scenarios"] = [
                    "upload",
                    "ingestion",
                    "retrieval",
                    "generation_recovery",
                ]
                measurement["acceptance_objectives"] = {
                    "approved_by": "fixture-owner",
                    "approved_at": measurement["started_at"],
                    "max_p95_ms": 250,
                    "max_error_rate": 0.01,
                    "min_headroom_percent": 20,
                }
            report["measurement_report"] = write_ref(
                root, f"{gate_id}-measurement.json", measurement
            )
        reports[gate_id] = report
        rows.append(
            {
                "id": gate_id,
                "status": "passed",
                "evidence": write_ref(root, f"{gate_id}.json", report),
            }
        )
    manifest = {"schema_version": 1, "release_scope": scope, "candidate": TARGET, "gates": rows}
    write_ref(root, "readiness.json", manifest)
    return manifest, reports


def evaluate(root, scope="invited"):
    return check(
        root=root,
        manifest="readiness.json",
        commit=SHA,
        environment="staging",
        profile="single-node-4c4g",
        scope=scope,
        now=NOW,
    )


def update_evidence(root, manifest, gate_id, report):
    row = next(row for row in manifest["gates"] if row["id"] == gate_id)
    row["evidence"] = write_ref(root, f"{gate_id}.json", report)
    write_ref(root, "readiness.json", manifest)


@pytest.mark.parametrize("scope", ["invited", "public_saas"])
def test_complete_evidence_only_qualifies_for_human_release_review(tmp_path, scope):
    evidence_set(tmp_path, scope)
    result = evaluate(tmp_path, scope)
    assert result["issues"] == []
    assert result["status"] == "eligible_for_release_review"
    assert result["deployment_authorized"] is False


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("omitted", "required_gate_missing"),
        ("duplicate", "duplicate_gate"),
        ("unknown", "unknown_gate"),
        ("blocked", "gate_not_passed"),
        ("scope", "manifest_scope_mismatch"),
        ("candidate", "candidate_binding_mismatch"),
    ],
)
def test_manifest_cannot_reduce_or_relabel_the_release_contract(tmp_path, mutation, code):
    manifest, _ = evidence_set(tmp_path)
    if mutation == "omitted":
        manifest["gates"].pop()
    elif mutation == "duplicate":
        manifest["gates"].append(manifest["gates"][0])
    elif mutation == "unknown":
        manifest["gates"].append({"id": "made_up"})
    elif mutation == "blocked":
        manifest["gates"][0]["status"] = "blocked"
    elif mutation == "scope":
        manifest["release_scope"] = "public_saas"
    else:
        manifest["candidate"] = {**TARGET, "commit_sha": "b" * 40}
    write_ref(tmp_path, "readiness.json", manifest)
    assert code in {item["code"] for item in evaluate(tmp_path)["issues"]}


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("failed", "evidence_not_passed"),
        ("stale", "evidence_not_current"),
        ("future", "evidence_not_current"),
        ("wrong_commit", "candidate_binding_mismatch"),
        ("wrong_profile", "candidate_binding_mismatch"),
        ("no_check", "required_checks_not_passed"),
        ("self_review", "independent_review_required"),
        ("no_review", "review_not_approved"),
        ("early_review", "invalid_review_time"),
        ("no_source", "source_reports_missing"),
        ("raw_failed", "source_report_not_passed"),
        ("raw_old_commit", "source_commit_mismatch"),
        ("raw_stale", "source_report_not_current"),
        ("raw_wrong_environment", "source_environment_mismatch"),
    ],
)
def test_pass_label_cannot_hide_missing_or_conflicting_evidence(tmp_path, mutation, code):
    manifest, reports = evidence_set(tmp_path)
    report = reports["business_journey"]
    if mutation == "failed":
        report["status"] = "failed"
    elif mutation == "stale":
        report["completed_at"] = (NOW - timedelta(days=8)).isoformat()
    elif mutation == "future":
        report["completed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    elif mutation == "wrong_commit":
        report["commit_sha"] = "b" * 40
    elif mutation == "wrong_profile":
        report["profile"] = "single-node-4c8g"
    elif mutation == "no_check":
        report["checks"].pop("github_login")
    elif mutation == "self_review":
        report["review"]["reviewed_by"] = " FIXTURE-OPERATOR "
    elif mutation == "no_review":
        report["review"]["status"] = "pending"
    elif mutation == "early_review":
        report["review"]["reviewed_at"] = (NOW - timedelta(days=1)).isoformat()
    elif mutation == "no_source":
        report["artifacts"] = []
    else:
        path = report["artifacts"][0]["path"]
        raw = json.loads((tmp_path / path).read_text())
        if mutation == "raw_failed":
            raw["status"] = "failed"
        elif mutation == "raw_old_commit":
            raw["commit_sha"] = "b" * 40
        elif mutation == "raw_stale":
            raw["completed_at"] = (NOW - timedelta(days=8)).isoformat()
        else:
            raw["environment"] = "local"
        report["artifacts"][0] = write_ref(tmp_path, path, raw)
    update_evidence(tmp_path, manifest, "business_journey", report)
    assert {"gate": "business_journey", "code": code} in evaluate(tmp_path)["issues"]


def test_hash_tampering_is_rejected(tmp_path):
    evidence_set(tmp_path)
    (tmp_path / "model_quality.json").write_text("{}")
    assert {"gate": "model_quality", "code": "evidence_digest_mismatch"} in evaluate(tmp_path)[
        "issues"
    ]


def test_readiness_only_capacity_cannot_support_business_capacity(tmp_path):
    manifest, reports = evidence_set(tmp_path)
    report = reports["capacity"]
    path = report["measurement_report"]["path"]
    raw = json.loads((tmp_path / path).read_text())
    raw["workload"]["scenarios"] = ["ready"]
    report["measurement_report"] = write_ref(tmp_path, path, raw)
    update_evidence(tmp_path, manifest, "capacity", report)
    assert {"gate": "capacity", "code": "business_capacity_required"} in evaluate(tmp_path)[
        "issues"
    ]


def test_recovery_reuses_real_objectives_and_fault_domain_validator(tmp_path):
    manifest, reports = evidence_set(tmp_path)
    report = reports["recovery"]
    path = report["measurement_report"]["path"]
    raw = json.loads((tmp_path / path).read_text())
    raw["recovery_scope"]["fault_domain_isolation_verified"] = False
    report["measurement_report"] = write_ref(tmp_path, path, raw)
    update_evidence(tmp_path, manifest, "recovery", report)
    assert {"gate": "recovery", "code": "measurement_contract_failed"} in evaluate(tmp_path)[
        "issues"
    ]


@pytest.mark.parametrize(
    "metric,value", [("p95_ms", 300), ("error_rate", 0.1), ("headroom_percent", 10)]
)
def test_capacity_pass_label_cannot_override_approved_limits(tmp_path, metric, value):
    manifest, reports = evidence_set(tmp_path)
    report = reports["capacity"]
    path = report["measurement_report"]["path"]
    raw = json.loads((tmp_path / path).read_text())
    raw["measurements"][metric] = value
    if metric == "p95_ms":
        raw["measurements"]["p99_ms"] = value + 10
    report["measurement_report"] = write_ref(tmp_path, path, raw)
    update_evidence(tmp_path, manifest, "capacity", report)
    assert {"gate": "capacity", "code": "capacity_objective_failed"} in evaluate(tmp_path)["issues"]


@pytest.mark.parametrize(
    "path", ["../outside.json", "C:/private.json", "/private.json", "evidence\\private.json"]
)
def test_manifest_path_cannot_escape_repository(tmp_path, path):
    result = run_check(tmp_path, manifest=path)
    assert result.returncode == 1
    assert json.loads(result.stdout)["issues"] == [
        {"gate": "manifest", "code": "unsafe_evidence_path"}
    ]


def test_public_scope_requires_payments_and_self_service(tmp_path):
    manifest, _ = evidence_set(tmp_path)
    manifest["release_scope"] = "public_saas"
    write_ref(tmp_path, "readiness.json", manifest)
    assert evaluate(tmp_path, "public_saas")["issues"] == [
        {"gate": "payments", "code": "required_gate_missing"},
        {"gate": "self_service", "code": "required_gate_missing"},
    ]
