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


def run_check(root, manifest="readiness.json", scope="invited", *extra):
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
            *extra,
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


def evaluate(root, scope="invited", **options):
    return check(
        root=root,
        manifest="readiness.json",
        commit=SHA,
        environment="staging",
        profile="single-node-4c4g",
        scope=scope,
        now=NOW,
        **options,
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


DEFERRED = {
    "recovery": ["database_and_objects", "independent_fault_domain", "rpo_rto"],
    "observability": ["backup_freshness"],
}


def scoped_evidence(root):
    manifest, reports = evidence_set(root)
    decision = {
        **TARGET,
        "schema_version": 1,
        "release_scope": "invited",
        "decision": "deferred",
        "decided_by": "fixture-owner",
        "recorded_by": "fixture-operator",
        "recorded_at": (NOW - timedelta(hours=1)).isoformat(),
        "reason": "Defer backup and disaster recovery for the invited release.",
        "source": "fixture approved scope record",
        "deferred_checks": DEFERRED,
    }
    manifest["deferral_decision"] = write_ref(root, "deferral.json", decision)
    for gate_id, checks in DEFERRED.items():
        row = next(row for row in manifest["gates"] if row["id"] == gate_id)
        row["status"] = "passed_with_deferrals"
        report = reports[gate_id]
        report["status"] = "passed_with_deferrals"
        report["checks"].update(dict.fromkeys(checks, "deferred"))
        if gate_id == "recovery":
            rollback = {
                **TARGET,
                "status": "passed",
                "evidence_type": "release_rollback",
                "completed_at": (NOW - timedelta(minutes=30)).isoformat(),
                "rollback_commit_sha": "b" * 40,
                "measurements": {"rollback_duration_seconds": 60.0},
                "checks": {"rollback_readiness": True, "restored_commit_verified": True},
            }
            report["measurement_report"] = write_ref(root, "rollback.json", rollback)
        update_evidence(root, manifest, gate_id, report)
    return manifest, reports, decision


def test_explicit_invited_deferral_keeps_rollback_and_is_never_a_full_pass(tmp_path):
    manifest, _, _ = scoped_evidence(tmp_path)
    result = evaluate(tmp_path, deferral_sha256=manifest["deferral_decision"]["sha256"])
    assert result["issues"] == []
    assert result["status"] == "eligible_for_scoped_release_review"
    assert result["deployment_authorized"] is False
    assert result["validated_scoped_gates"] == ["observability", "recovery"]
    assert result["deferred_checks"] == DEFERRED
    assert set(result["validated_gates"]) == set(REQUIRED_CHECKS) - set(DEFERRED) - {
        "payments",
        "self_service",
    }


@pytest.mark.parametrize("pin", [None, "", "bad", "b" * 64])
def test_manifest_cannot_approve_its_own_deferral(tmp_path, pin):
    scoped_evidence(tmp_path)
    result = evaluate(tmp_path, deferral_sha256=pin)
    assert result["status"] == "blocked"
    assert result["validated_gates"] == result["validated_scoped_gates"] == []
    assert result["issues"] == [
        {
            "gate": "manifest",
            "code": "deferral_pin_mismatch" if pin == "b" * 64 else "deferral_pin_required",
        }
    ]


def test_pin_without_decision_is_rejected(tmp_path):
    evidence_set(tmp_path)
    result = evaluate(tmp_path, deferral_sha256="b" * 64)
    assert result["issues"] == [{"gate": "manifest", "code": "invalid_document"}]


def test_invited_deferral_cannot_be_used_for_public_saas(tmp_path):
    manifest, _, _ = scoped_evidence(tmp_path)
    manifest["release_scope"] = "public_saas"
    write_ref(tmp_path, "readiness.json", manifest)
    result = evaluate(
        tmp_path, "public_saas", deferral_sha256=manifest["deferral_decision"]["sha256"]
    )
    assert result["issues"] == [{"gate": "manifest", "code": "deferral_scope_not_allowed"}]


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("schema", "invalid_deferral_decision"),
        ("passed", "invalid_deferral_decision"),
        ("scope", "invalid_deferral_decision"),
        ("commit", "candidate_binding_mismatch"),
        ("environment", "candidate_binding_mismatch"),
        ("profile", "candidate_binding_mismatch"),
        ("identity", "missing_accountable_identity"),
        ("source", "missing_accountable_identity"),
        ("future", "invalid_deferral_time"),
        ("rollback", "deferral_checks_not_allowed"),
        ("gate", "deferral_checks_not_allowed"),
        ("subset", "deferral_checks_not_allowed"),
        ("duplicate", "deferral_checks_not_allowed"),
        ("invalid_type", "deferral_checks_not_allowed"),
    ],
)
def test_even_pinned_deferrals_have_fixed_scope_and_provenance(tmp_path, mutation, code):
    manifest, _, decision = scoped_evidence(tmp_path)
    # Reload rather than mutating shared fixture constants.
    decision = json.loads((tmp_path / "deferral.json").read_bytes())
    if mutation == "schema":
        decision["schema_version"] = 2
    elif mutation == "passed":
        decision["decision"] = "passed"
    elif mutation == "scope":
        decision["release_scope"] = "public_saas"
    elif mutation == "commit":
        decision["commit_sha"] = "b" * 40
    elif mutation == "environment":
        decision["environment"] = "production"
    elif mutation == "profile":
        decision["profile"] = "single-node-4c8g"
    elif mutation == "identity":
        decision["decided_by"] = " "
    elif mutation == "source":
        del decision["source"]
    elif mutation == "future":
        decision["recorded_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "rollback":
        decision["deferred_checks"]["recovery"].append("rollback")
    elif mutation == "gate":
        decision["deferred_checks"]["model_quality"] = ["semantic_review"]
    elif mutation == "subset":
        decision["deferred_checks"]["recovery"].pop()
    elif mutation == "duplicate":
        decision["deferred_checks"]["recovery"].append("rpo_rto")
    else:
        decision["deferred_checks"]["recovery"] = [{}]
    manifest["deferral_decision"] = write_ref(tmp_path, "deferral.json", decision)
    write_ref(tmp_path, "readiness.json", manifest)
    result = evaluate(tmp_path, deferral_sha256=manifest["deferral_decision"]["sha256"])
    assert result["issues"] == [{"gate": "manifest", "code": code}]


def test_deferral_bytes_cannot_change_after_pin(tmp_path):
    manifest, _, _ = scoped_evidence(tmp_path)
    (tmp_path / "deferral.json").write_text("{}")
    result = evaluate(tmp_path, deferral_sha256=manifest["deferral_decision"]["sha256"])
    assert result["issues"] == [{"gate": "manifest", "code": "evidence_digest_mismatch"}]


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("gate_full_pass", "gate_not_passed"),
        ("report_full_pass", "evidence_not_passed"),
        ("deferred_true", "deferred_checks_mislabeled"),
        ("rollback_deferred", "required_checks_not_passed"),
        ("self_review", "independent_review_required"),
        ("stale", "evidence_not_current"),
        ("failed_source", "source_report_not_passed"),
        ("missing_measurement", "invalid_document"),
        ("wrong_measurement", "rollback_measurement_required"),
        ("rollback_commit", "invalid_rollback_target"),
        ("rollback_failed", "rollback_checks_not_passed"),
        ("restored_unverified", "rollback_checks_not_passed"),
        ("rollback_unknown_time", "invalid_rollback_measurement"),
        ("rollback_boolean_time", "invalid_rollback_measurement"),
        ("rollback_old", "source_report_not_current"),
        ("rollback_wrong_candidate", "candidate_binding_mismatch"),
    ],
)
def test_scoped_recovery_still_requires_honest_current_reviewed_rollback(tmp_path, mutation, code):
    manifest, reports, _ = scoped_evidence(tmp_path)
    report = reports["recovery"]
    row = next(row for row in manifest["gates"] if row["id"] == "recovery")
    if mutation == "gate_full_pass":
        row["status"] = "passed"
    elif mutation == "report_full_pass":
        report["status"] = "passed"
    elif mutation == "deferred_true":
        report["checks"]["rpo_rto"] = True
    elif mutation == "rollback_deferred":
        report["checks"]["rollback"] = "deferred"
    elif mutation == "self_review":
        report["review"]["reviewed_by"] = " FIXTURE-OPERATOR "
    elif mutation == "stale":
        report["completed_at"] = (NOW - timedelta(days=8)).isoformat()
    elif mutation == "failed_source":
        raw = json.loads((tmp_path / "recovery-raw.json").read_bytes())
        raw["status"] = "failed"
        report["artifacts"] = [write_ref(tmp_path, "recovery-raw.json", raw)]
    elif mutation == "missing_measurement":
        del report["measurement_report"]
    else:
        raw = json.loads((tmp_path / "rollback.json").read_bytes())
        if mutation == "wrong_measurement":
            raw["evidence_type"] = "recovery"
        elif mutation == "rollback_commit":
            raw["rollback_commit_sha"] = SHA
        elif mutation == "rollback_failed":
            raw["checks"]["rollback_readiness"] = False
        elif mutation == "restored_unverified":
            raw["checks"]["restored_commit_verified"] = False
        elif mutation == "rollback_unknown_time":
            raw["measurements"]["rollback_duration_seconds"] = None
        elif mutation == "rollback_boolean_time":
            raw["measurements"]["rollback_duration_seconds"] = True
        elif mutation == "rollback_old":
            raw["completed_at"] = (NOW - timedelta(days=8)).isoformat()
        else:
            raw["commit_sha"] = "b" * 40
        report["measurement_report"] = write_ref(tmp_path, "rollback.json", raw)
    update_evidence(tmp_path, manifest, "recovery", report)
    result = evaluate(tmp_path, deferral_sha256=manifest["deferral_decision"]["sha256"])
    assert {"gate": "recovery", "code": code} in result["issues"]
    assert "recovery" not in result["validated_scoped_gates"]


def test_scoped_observability_still_requires_actual_recovery_delivery(tmp_path):
    manifest, reports, _ = scoped_evidence(tmp_path)
    reports["observability"]["checks"]["recovery_delivered"] = "deferred"
    update_evidence(tmp_path, manifest, "observability", reports["observability"])
    result = evaluate(tmp_path, deferral_sha256=manifest["deferral_decision"]["sha256"])
    assert {"gate": "observability", "code": "required_checks_not_passed"} in result["issues"]


def test_cli_scoped_pass_and_missing_pin_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "NOW", datetime.now(UTC) - timedelta(seconds=1))
    manifest, _, _ = scoped_evidence(tmp_path)
    result = run_check(
        tmp_path,
        "readiness.json",
        "invited",
        "--deferral-sha256",
        manifest["deferral_decision"]["sha256"],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "eligible_for_scoped_release_review"
    missing_pin = run_check(tmp_path)
    assert missing_pin.returncode == 1
    assert json.loads(missing_pin.stdout)["issues"] == [
        {"gate": "manifest", "code": "deferral_pin_required"}
    ]
