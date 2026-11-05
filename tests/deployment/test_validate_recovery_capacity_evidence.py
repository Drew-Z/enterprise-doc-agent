from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.validate_recovery_capacity_evidence import (
    EvidenceValidationError,
    _canonical_sha256,
    _snapshot_identity,
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


def _json_artifact(root: Path, name: str, kind: str, payload: object) -> dict[str, str]:
    path = root / "artifacts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _analysis_resource_snapshot(
    *, images: dict[str, str], captured_at: str, observations: int
) -> dict[str, object]:
    roles = (*images, "redis")
    deployments = [
        {
            "role": role,
            "uid_sha256": role + "-deployment",
            "template_sha256": role + "-template",
            "containers": [{"configured_digest": images.get(role, "sha256:" + "9" * 64)}],
            "desired_replicas": 1,
            "ready_replicas": 1,
            "updated_replicas": 1,
            "generation": 1,
            "observed_generation": 1,
        }
        for role in roles
    ]
    pods = [
        {
            "role": role,
            "uid_sha256": "2" * 64 if role == "api" else role + "-pod",
            "phase": "Running",
            "containers": [
                {
                    "configured_digest": images.get(role, "sha256:" + "9" * 64),
                    "running_digest": images.get(role, "sha256:" + "9" * 64),
                    "ready": True,
                    "last_oom": False,
                }
            ],
        }
        for role in roles
    ]
    return {
        "captured_at": captured_at,
        "namespace_sha256": "3" * 64,
        "inventory": {
            "cluster_sha256": "4" * 64,
            "configuration_sha256": "5" * 64,
            "nodes": [{"uid_sha256": "6" * 64, "cpu_capacity": "4", "memory_capacity": "4Gi"}],
            "deployments": deployments,
            "image_relationships": [],
            "pods": pods,
        },
        "host": {"cpu_count": 4, "memory_total_bytes": 4 * 1024**3},
        "traffic_proof": {
            "status": "observed" if observations else "not_observed",
            "correlation_sha256": "1" * 64,
            "api_pod_uid_sha256": "2" * 64,
            "observations": observations,
        },
        "errors": [],
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
    report["recovery_objectives"] = {
        "rpo_seconds": 60.0,
        "rto_seconds": 300.0,
        "approved_by": "service-owner",
        "approved_at": "2026-07-20T09:55:00+08:00",
    }
    report["recovery_timeline"] = {
        "latest_recoverable_data_at": "2026-07-20T09:59:30+08:00",
        "failure_declared_at": "2026-07-20T10:00:00+08:00",
        "application_restored_at": "2026-07-20T10:03:00+08:00",
    }
    report["review"] = {
        "reviewed_by": "recovery-reviewer",
        "reviewed_at": "2026-07-20T10:11:00+08:00",
        "production_like": True,
    }
    report["recovery_scope"] = {
        "source_environment": "production-like-primary",
        "recovery_environment": "production-like-isolated-recovery",
        "fault_domain_isolation_verified": True,
        "live_environment_mutated": False,
    }
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
            "cpu": {"sample_count": 3, "peak_percent": 65.0},
            "memory": {"sample_count": 3, "peak_percent": 58.0},
            "database_pool": {"sample_count": 3, "peak_percent": 72.0},
            "queue": {"sample_count": 3, "max_age_seconds": 1.2},
            "redis": {"sample_count": 3, "peak_connections": 20},
            "object_store": {"sample_count": 3, "p95_ms": 45.0},
            "model": {"sample_count": 3, "p95_ms": 110.0},
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
            "gpu": {"sample_count": 3, "utilization_percent": 82.0},
            "gpu_memory": {"sample_count": 3, "peak_percent": 78.0},
            "kv_cache": {"sample_count": 3, "peak_percent": 75.0},
            "queue": {"sample_count": 3, "max_age_seconds": 0.8},
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


def test_passed_recovery_requires_predeclared_objectives_and_measured_timeline(
    tmp_path: Path,
) -> None:
    report = _passed_recovery(tmp_path)
    validate_evidence(report, root=tmp_path)

    missing_objectives = deepcopy(report)
    missing_objectives.pop("recovery_objectives")
    with pytest.raises(EvidenceValidationError, match="recovery_objectives"):
        validate_evidence(missing_objectives, root=tmp_path)

    late_approval = deepcopy(report)
    late_approval["recovery_objectives"]["approved_at"] = "2026-07-20T10:00:01+08:00"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="approved before"):
        validate_evidence(late_approval, root=tmp_path)

    mismatched_rpo = deepcopy(report)
    mismatched_rpo["measurements"]["rpo_seconds"] = 29.0  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="calculated RPO"):
        validate_evidence(mismatched_rpo, root=tmp_path)

    missed_rto = deepcopy(report)
    missed_rto["recovery_objectives"]["rto_seconds"] = 120.0  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="RTO objective"):
        validate_evidence(missed_rto, root=tmp_path)


def test_passed_recovery_requires_completed_independent_review(tmp_path: Path) -> None:
    report = _passed_recovery(tmp_path)

    same_operator = deepcopy(report)
    same_operator["review"]["reviewed_by"] = same_operator["operator"]  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="independent"):
        validate_evidence(same_operator, root=tmp_path)

    early_review = deepcopy(report)
    early_review["review"]["reviewed_at"] = "2026-07-20T10:09:59+08:00"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="after completion"):
        validate_evidence(early_review, root=tmp_path)

    not_production_like = deepcopy(report)
    not_production_like["review"]["production_like"] = False  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="production_like"):
        validate_evidence(not_production_like, root=tmp_path)

    same_environment = deepcopy(report)
    same_environment["recovery_scope"]["recovery_environment"] = same_environment["recovery_scope"][
        "source_environment"
    ]  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="must differ"):
        validate_evidence(same_environment, root=tmp_path)

    live_mutation = deepcopy(report)
    live_mutation["recovery_scope"]["live_environment_mutated"] = True  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="must not mutate"):
        validate_evidence(live_mutation, root=tmp_path)


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

    zero_samples = deepcopy(report)
    zero_samples["measurements"]["telemetry"]["queue"]["sample_count"] = 0  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="sample_count"):
        validate_evidence(zero_samples, root=tmp_path)

    single_run = deepcopy(report)
    single_run["workload"]["repetitions"] = 1  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="repetitions"):
        validate_evidence(single_run, root=tmp_path)


def test_analysis_4c4g_capacity_requires_machine_and_release_evidence(tmp_path: Path) -> None:
    report = _passed_capacity(tmp_path)
    report["workload"]["name"] = "analysis-4c4g"  # type: ignore[index]
    report["workload"]["base_url"] = "https://staging.example.com"  # type: ignore[index]
    report["targets"] = {"cpu_cores": 4, "memory_bytes": 4 * 1024**3}
    report["environment"]["resource_evidence_status"] = "verified"  # type: ignore[index]
    report["environment"]["target_binding_status"] = "verified"  # type: ignore[index]
    report["http_requests"] = 42
    report["image_digest"] = {
        service: "sha256:" + character * 64
        for service, character in zip(
            ("api", "worker", "consumer", "web"), ("a", "b", "c", "d"), strict=True
        )
    }
    report["release_identity"] = {
        "commit_sha": report["commit_sha"],
        "repository": "example/enterprise-doc-agent",
        "ref": "refs/tags/v1",
        "run_id": "123456",
        "run_attempt": "1",
        "workflow_ref": "example/enterprise-doc-agent/.github/workflows/ci.yml@v1",
        "image_digests": report["image_digest"],
    }
    snapshots = [
        _analysis_resource_snapshot(
            images=report["image_digest"],  # type: ignore[arg-type]
            captured_at="2026-07-20T09:59:59+08:00",
            observations=0,
        ),
        _analysis_resource_snapshot(
            images=report["image_digest"],  # type: ignore[arg-type]
            captured_at="2026-07-20T10:10:01+08:00",
            observations=42,
        ),
    ]
    report["resource_evidence"] = {
        "status": "verified",
        "sample_count": 2,
        "identity_sha256": _snapshot_identity(snapshots[0]),
        "samples_sha256": _canonical_sha256(snapshots),
        "cpu_cores": 4,
        "memory_bytes": 4 * 1024**3,
        "image_digests": report["image_digest"],
        "first_captured_at": "2026-07-20T09:59:59+08:00",
        "last_captured_at": "2026-07-20T10:10:01+08:00",
        "target_binding": {
            "status": "verified",
            "origin": "https://staging.example.com",
            "correlation_sha256": "1" * 64,
            "api_pod_uid_sha256": "2" * 64,
            "observations": 42,
            "expected_http_requests": 42,
        },
    }
    resources_artifact = _json_artifact(
        tmp_path,
        "resources.json",
        "analysis-capacity-resources",
        {"snapshots": snapshots},
    )
    release_artifact = _json_artifact(
        tmp_path,
        "release.json",
        "analysis-capacity-release-manifest",
        {
            "schema_version": 1,
            "status": "passed",
            "release": {
                "commit_sha": report["commit_sha"],
                "repository": "example/enterprise-doc-agent",
                "ref": "refs/tags/v1",
                "run_id": "123456",
                "run_attempt": "1",
                "workflow_ref": "example/enterprise-doc-agent/.github/workflows/ci.yml@v1",
            },
            "images": {
                service: {"digest": digest}
                for service, digest in report["image_digest"].items()  # type: ignore[union-attr]
            },
        },
    )
    report["release_identity"]["sha256"] = release_artifact["sha256"]  # type: ignore[index]
    report["artifacts"].extend([resources_artifact, release_artifact])  # type: ignore[union-attr]
    validate_evidence(report, root=tmp_path)

    release_payload = json.loads((tmp_path / release_artifact["path"]).read_bytes())
    for field in ("repository", "ref", "run_id", "run_attempt", "workflow_ref"):
        for invalid in (None, "", " ", 123):
            incomplete_release = deepcopy(release_payload)
            if invalid is None:
                incomplete_release["release"].pop(field)
            else:
                incomplete_release["release"][field] = invalid
            incomplete_artifact = _json_artifact(
                tmp_path,
                "release-incomplete.json",
                "analysis-capacity-release-manifest",
                incomplete_release,
            )
            incomplete_report = deepcopy(report)
            incomplete_report["artifacts"][-1] = incomplete_artifact  # type: ignore[index]
            incomplete_report["release_identity"]["sha256"] = incomplete_artifact["sha256"]  # type: ignore[index]
            if field in ("repository", "ref"):
                incomplete_report["release_identity"][field] = invalid  # type: ignore[index]
            with pytest.raises(
                EvidenceValidationError, match=rf"release manifest release\.{field}"
            ):
                validate_evidence(incomplete_report, root=tmp_path)

    for field in ("repository", "ref", "run_id", "run_attempt", "workflow_ref"):
        mismatched_identity = deepcopy(report)
        mismatched_identity["release_identity"][field] = "different"  # type: ignore[index]
        with pytest.raises(EvidenceValidationError, match="release artifact identity"):
            validate_evidence(mismatched_identity, root=tmp_path)

    missing = deepcopy(report)
    missing.pop("resource_evidence")
    with pytest.raises(EvidenceValidationError, match="resource_evidence"):
        validate_evidence(missing, root=tmp_path)

    wrong_commit = deepcopy(report)
    wrong_commit["release_identity"]["commit_sha"] = "0" * 40  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="release commit"):
        validate_evidence(wrong_commit, root=tmp_path)

    wrong_target = deepcopy(report)
    wrong_target["resource_evidence"]["target_binding"]["origin"] = (  # type: ignore[index]
        "https://other.example.com"
    )
    with pytest.raises(EvidenceValidationError, match="target binding"):
        validate_evidence(wrong_target, root=tmp_path)

    partial_traffic = deepcopy(report)
    partial_traffic["resource_evidence"]["target_binding"]["observations"] = 41  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="target binding"):
        validate_evidence(partial_traffic, root=tmp_path)

    artifact_traffic = deepcopy(report)
    tampered_snapshots = deepcopy(snapshots)
    tampered_snapshots[-1]["traffic_proof"]["observations"] = 41  # type: ignore[index]
    artifact_traffic["resource_evidence"]["samples_sha256"] = _canonical_sha256(  # type: ignore[index]
        tampered_snapshots
    )
    updated_artifact = _json_artifact(
        tmp_path,
        "resources.json",
        "analysis-capacity-resources",
        {"snapshots": tampered_snapshots},
    )
    next(
        item
        for item in artifact_traffic["artifacts"]  # type: ignore[union-attr]
        if item["kind"] == "analysis-capacity-resources"
    )["sha256"] = updated_artifact["sha256"]
    with pytest.raises(EvidenceValidationError, match="artifact target binding"):
        validate_evidence(artifact_traffic, root=tmp_path)


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
