from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.validate_recovery_capacity_evidence import (
    EvidenceValidationError,
    validate_evidence,
)


def _artifact(root: Path) -> dict[str, str]:
    path = root / "artifacts" / "run.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("reviewed evidence\n", encoding="utf-8")
    return {
        "path": "artifacts/run.log",
        "kind": "execution-log",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _common(root: Path, *, evidence_type: str, status: str) -> dict[str, object]:
    external_execution = status != "blocked_external"
    report: dict[str, object] = {
        "schema_version": 1,
        "evidence_type": evidence_type,
        "evidence_id": f"test-{evidence_type}",
        "milestone": "M6" if evidence_type == "recovery" else "M5",
        "requirement_ids": ["TEST-R1"],
        "status": status,
        "environment": {
            "name": "staging" if external_execution else "unavailable",
            "provider": "example-cloud" if external_execution else None,
            "region": "cn-test-1" if external_execution else None,
            "cluster": "cluster-test" if external_execution else None,
            "external_execution": external_execution,
        },
        "commit_sha": "a" * 40 if external_execution else None,
        "image_digest": "sha256:" + "b" * 64 if external_execution else None,
        "operator": "release-owner",
        "started_at": "2026-07-20T10:00:00+08:00",
        "completed_at": "2026-07-20T10:10:00+08:00",
        "command_or_procedure": ["execute reviewed runbook"],
        "measurements": {},
        "limitations": ["This report is limited to the named environment and image."],
        "artifacts": [_artifact(root)],
        "owner": "delivery-owner",
    }
    if status == "blocked_external":
        report["blocking_reason"] = "No isolated external target is available."
        report["prerequisites"] = ["Provision an isolated target."]
    return report


def _passed_recovery(root: Path) -> dict[str, object]:
    report = _common(root, evidence_type="recovery", status="passed")
    report["measurements"] = {
        "rpo_seconds": 30.0,
        "rto_seconds": 180.0,
        "backup_age_seconds_at_restore": 20.0,
        "restore_duration_seconds": 120.0,
        "rollback_duration_seconds": 60.0,
    }
    report["smoke_checks"] = [
        {"name": name, "status": "passed"}
        for name in (
            "backup_integrity",
            "data_integrity",
            "application_readiness",
            "rollback_readiness",
        )
    ]
    return report


def _passed_capacity(root: Path) -> dict[str, object]:
    report = _common(root, evidence_type="capacity", status="passed")
    report["capacity_profile"] = "application"
    report["workload"] = {
        "phases": ["ramp", "steady_state", "burst", "recovery"],
        "repetitions": 3,
    }
    report["measurements"] = {
        "p50_ms": 80.0,
        "p95_ms": 180.0,
        "p99_ms": 250.0,
        "error_rate": 0.001,
        "throughput_per_second": 120.0,
        "headroom_percent": 35.0,
        "bottleneck": "database connection pool",
        "telemetry": {
            "cpu": {"peak_percent": 65.0},
            "memory": {"peak_percent": 58.0},
            "database_pool": {"peak_percent": 72.0},
            "queue": {"max_age_seconds": 1.2},
            "redis": {"peak_connections": 20},
            "object_store": {"p95_ms": 45.0},
            "model": {"p95_ms": 110.0},
        },
    }
    return report


def _passed_model_capacity(root: Path) -> dict[str, object]:
    report = _common(root, evidence_type="capacity", status="passed")
    report["milestone"] = "M7"
    report["capacity_profile"] = "model"
    report["workload"] = {
        "phases": ["warmup", "steady_state", "burst", "recovery"],
        "repetitions": 3,
    }
    report["measurements"] = {
        "ttft_p50_ms": 40.0,
        "ttft_p95_ms": 80.0,
        "ttft_p99_ms": 100.0,
        "tpot_p50_ms": 8.0,
        "tpot_p95_ms": 12.0,
        "tpot_p99_ms": 15.0,
        "tokens_per_second": 900.0,
        "error_rate": 0.0,
        "headroom_percent": 20.0,
        "bottleneck": "KV cache",
        "telemetry": {
            "gpu": {"utilization_percent": 82.0},
            "gpu_memory": {"peak_percent": 78.0},
            "kv_cache": {"peak_percent": 75.0},
            "queue": {"max_age_seconds": 0.8},
        },
    }
    return report


def test_blocked_external_requires_reason_and_never_claims_execution(tmp_path: Path) -> None:
    report = _common(tmp_path, evidence_type="recovery", status="blocked_external")
    validate_evidence(report, root=tmp_path)

    missing_reason = deepcopy(report)
    missing_reason.pop("blocking_reason")
    with pytest.raises(EvidenceValidationError, match="blocking_reason"):
        validate_evidence(missing_reason, root=tmp_path)

    false_pass = deepcopy(report)
    false_pass["status"] = "passed"
    with pytest.raises(EvidenceValidationError, match="external execution"):
        validate_evidence(false_pass, root=tmp_path)


def test_passed_recovery_requires_rpo_rto_and_all_smoke_checks(tmp_path: Path) -> None:
    report = _passed_recovery(tmp_path)
    validate_evidence(report, root=tmp_path)

    missing_rto = deepcopy(report)
    del missing_rto["measurements"]["rto_seconds"]  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="rto_seconds"):
        validate_evidence(missing_rto, root=tmp_path)

    failed_smoke = deepcopy(report)
    failed_smoke["smoke_checks"][0]["status"] = "failed"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="smoke checks"):
        validate_evidence(failed_smoke, root=tmp_path)

    duplicate_smoke = deepcopy(report)
    duplicate_smoke["smoke_checks"][0]["status"] = "failed"  # type: ignore[index]
    duplicate_smoke["smoke_checks"].append(  # type: ignore[union-attr]
        {"name": "backup_integrity", "status": "passed"}
    )
    with pytest.raises(EvidenceValidationError, match="duplicate smoke check"):
        validate_evidence(duplicate_smoke, root=tmp_path)


def test_passed_capacity_requires_repeated_phases_and_dependency_telemetry(
    tmp_path: Path,
) -> None:
    report = _passed_capacity(tmp_path)
    validate_evidence(report, root=tmp_path)

    missing_telemetry = deepcopy(report)
    del missing_telemetry["measurements"]["telemetry"]["queue"]  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="queue"):
        validate_evidence(missing_telemetry, root=tmp_path)

    empty_telemetry = deepcopy(report)
    empty_telemetry["measurements"]["telemetry"]["queue"] = None  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match=r"telemetry\.queue"):
        validate_evidence(empty_telemetry, root=tmp_path)

    single_run = deepcopy(report)
    single_run["workload"]["repetitions"] = 1  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="repetitions"):
        validate_evidence(single_run, root=tmp_path)


def test_artifact_hash_and_immutable_identity_are_verified(tmp_path: Path) -> None:
    report = _passed_recovery(tmp_path)
    report["artifacts"][0]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="SHA-256"):
        validate_evidence(report, root=tmp_path)

    report = _passed_recovery(tmp_path)
    report["image_digest"] = "latest"
    with pytest.raises(EvidenceValidationError, match="image_digest"):
        validate_evidence(report, root=tmp_path)


def test_model_capacity_requires_warmup_and_gpu_telemetry(tmp_path: Path) -> None:
    report = _passed_model_capacity(tmp_path)
    validate_evidence(report, root=tmp_path)

    missing_kv_cache = deepcopy(report)
    del missing_kv_cache["measurements"]["telemetry"]["kv_cache"]  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="kv_cache"):
        validate_evidence(missing_kv_cache, root=tmp_path)

    blocked = _common(tmp_path, evidence_type="capacity", status="blocked_external")
    blocked["capacity_profile"] = "model"
    blocked["workload"] = {
        "phases": ["warmup", "steady_state", "burst", "recovery"],
        "repetitions": 1,
    }
    validate_evidence(blocked, root=tmp_path)
