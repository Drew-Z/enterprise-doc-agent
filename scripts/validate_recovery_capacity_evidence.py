from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

STATUS_VALUES = {"passed", "failed", "blocked_external"}
EVIDENCE_TYPES = {"recovery", "capacity"}
CAPACITY_PROFILES = {"application", "model"}
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ARTIFACT_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvidenceValidationError(ValueError):
    """Raised when a delivery evidence record cannot support its status."""


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceValidationError(f"{label} must be an object")
    return value


def _require_non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _require_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvidenceValidationError(f"{label} must be a non-empty list")
    result = [_require_non_empty_string(item, f"{label} item") for item in value]
    if not result:
        raise EvidenceValidationError(f"{label} must be a non-empty list")
    return result


def _number(value: object, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceValidationError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise EvidenceValidationError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise EvidenceValidationError(f"{label} must be >= {minimum}")
    return result


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceValidationError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise EvidenceValidationError(f"{label} must include a timezone")
    return parsed


def _validate_commit(value: object) -> None:
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise EvidenceValidationError("commit_sha must be a full immutable commit SHA")


def _validate_image_digest(value: object, *, required: bool) -> None:
    if value is None:
        if required:
            raise EvidenceValidationError("image_digest is required for executed evidence")
        return
    if isinstance(value, str):
        digests = [value]
    elif isinstance(value, Mapping):
        digests = list(value.values())
        if not digests:
            raise EvidenceValidationError("image_digest mapping must not be empty")
    else:
        raise EvidenceValidationError("image_digest must be a digest or service-to-digest map")
    for digest in digests:
        if not isinstance(digest, str) or DIGEST_PATTERN.fullmatch(digest) is None:
            raise EvidenceValidationError("image_digest must contain immutable sha256 digests")


def _artifact_path(root: Path, value: object) -> Path:
    relative = _require_non_empty_string(value, "artifact.path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise EvidenceValidationError("artifact.path must be a safe repository-relative path")
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise EvidenceValidationError("artifact.path escapes the evidence root") from error
    if not candidate.is_file():
        raise EvidenceValidationError(f"artifact does not exist: {relative}")
    return candidate


def _validate_artifacts(report: Mapping[str, Any], root: Path) -> None:
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)) or not artifacts:
        raise EvidenceValidationError("artifacts must be a non-empty list")
    for item in artifacts:
        artifact = _require_mapping(item, "artifact")
        path = _artifact_path(root, artifact.get("path"))
        _require_non_empty_string(artifact.get("kind"), "artifact.kind")
        declared = artifact.get("sha256")
        if not isinstance(declared, str) or ARTIFACT_SHA_PATTERN.fullmatch(declared) is None:
            raise EvidenceValidationError("artifact SHA-256 must be 64 lowercase hex characters")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != declared:
            raise EvidenceValidationError(f"artifact SHA-256 mismatch: {artifact['path']}")


def _has_numeric_measurement(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return any(_has_numeric_measurement(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_numeric_measurement(item) for item in value)
    return False


def _validate_telemetry(telemetry: Mapping[str, Any], required: set[str]) -> None:
    missing = required - telemetry.keys()
    if missing:
        raise EvidenceValidationError(
            "measurements.telemetry missing: " + ", ".join(sorted(missing))
        )
    for name in required:
        value = telemetry[name]
        if not isinstance(value, Mapping) or not value:
            raise EvidenceValidationError(
                f"measurements.telemetry.{name} must be a non-empty object"
            )
        if not _has_numeric_measurement(value):
            raise EvidenceValidationError(
                f"measurements.telemetry.{name} must contain numeric measured data"
            )


def _validate_common(report: Mapping[str, Any], root: Path) -> str:
    if report.get("schema_version") != 1:
        raise EvidenceValidationError("schema_version must be 1")
    evidence_type = _require_non_empty_string(report.get("evidence_type"), "evidence_type")
    if evidence_type not in EVIDENCE_TYPES:
        raise EvidenceValidationError("evidence_type must be recovery or capacity")
    _require_non_empty_string(report.get("evidence_id"), "evidence_id")
    _require_non_empty_string(report.get("milestone"), "milestone")
    _require_string_list(report.get("requirement_ids"), "requirement_ids")
    status = _require_non_empty_string(report.get("status"), "status")
    if status not in STATUS_VALUES:
        raise EvidenceValidationError(f"status must be one of {sorted(STATUS_VALUES)}")

    environment = _require_mapping(report.get("environment"), "environment")
    _require_non_empty_string(environment.get("name"), "environment.name")
    if not isinstance(environment.get("external_execution"), bool):
        raise EvidenceValidationError("environment.external_execution must be boolean")
    executed = environment["external_execution"]
    if status == "blocked_external" and executed:
        raise EvidenceValidationError("blocked_external evidence cannot claim external execution")
    if status != "blocked_external" and not executed:
        raise EvidenceValidationError("passed/failed evidence requires external execution")
    for field in ("provider", "region", "cluster"):
        value = environment.get(field)
        if status != "blocked_external":
            _require_non_empty_string(value, f"environment.{field}")
        elif value is not None and not isinstance(value, str):
            raise EvidenceValidationError(f"environment.{field} must be a string or null")

    if status == "blocked_external":
        _require_non_empty_string(report.get("blocking_reason"), "blocking_reason")
        _require_string_list(report.get("prerequisites"), "prerequisites")

    commit_sha = report.get("commit_sha")
    if status != "blocked_external":
        _validate_commit(commit_sha)
    elif commit_sha is not None:
        _validate_commit(commit_sha)
    _validate_image_digest(report.get("image_digest"), required=status != "blocked_external")

    _require_non_empty_string(report.get("operator"), "operator")
    started = _timestamp(report.get("started_at"), "started_at")
    completed = _timestamp(report.get("completed_at"), "completed_at")
    if completed < started:
        raise EvidenceValidationError("completed_at must not precede started_at")
    procedure = report.get("command_or_procedure")
    if not isinstance(procedure, Sequence) or isinstance(procedure, (str, bytes)) or not procedure:
        raise EvidenceValidationError("command_or_procedure must be a non-empty list")
    _require_mapping(report.get("measurements"), "measurements")
    _require_string_list(report.get("limitations"), "limitations")
    _require_non_empty_string(report.get("owner"), "owner")
    _validate_artifacts(report, root)
    return status


def _validate_recovery(report: Mapping[str, Any], status: str) -> None:
    measurements = _require_mapping(report["measurements"], "measurements")
    required_measurements = (
        "rpo_seconds",
        "rto_seconds",
        "backup_age_seconds_at_restore",
        "restore_duration_seconds",
        "rollback_duration_seconds",
    )
    if status == "passed":
        for name in required_measurements:
            _number(measurements.get(name), f"measurements.{name}", minimum=0)
        smoke_checks = report.get("smoke_checks")
        if not isinstance(smoke_checks, Sequence) or isinstance(smoke_checks, (str, bytes)):
            raise EvidenceValidationError("smoke_checks must be a list")
        required_names = {
            "backup_integrity",
            "data_integrity",
            "application_readiness",
            "rollback_readiness",
        }
        observed: dict[str, str] = {}
        for item in smoke_checks:
            check = _require_mapping(item, "smoke_check")
            name = _require_non_empty_string(check.get("name"), "smoke_check.name")
            if name in observed:
                raise EvidenceValidationError(f"duplicate smoke check: {name}")
            observed[name] = _require_non_empty_string(check.get("status"), "smoke_check.status")
        if required_names - observed.keys():
            missing = ", ".join(sorted(required_names - observed.keys()))
            raise EvidenceValidationError(f"smoke checks missing: {missing}")
        if any(observed[name] != "passed" for name in required_names):
            raise EvidenceValidationError("all required smoke checks must be passed")


def _validate_capacity(report: Mapping[str, Any], status: str) -> None:
    profile = _require_non_empty_string(report.get("capacity_profile"), "capacity_profile")
    if profile not in CAPACITY_PROFILES:
        raise EvidenceValidationError("capacity_profile must be application or model")
    workload = _require_mapping(report.get("workload"), "workload")
    phases_value = workload.get("phases")
    if not isinstance(phases_value, Sequence) or isinstance(phases_value, (str, bytes)):
        raise EvidenceValidationError("workload.phases must be a list")
    phases = {_require_non_empty_string(item, "workload phase") for item in phases_value}
    repetitions = workload.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise EvidenceValidationError("workload.repetitions must be a positive integer")
    if status != "passed":
        return
    if repetitions < 2:
        raise EvidenceValidationError("workload.repetitions must be >= 2 for passed evidence")
    required_phases = (
        {"ramp", "steady_state", "burst", "recovery"}
        if profile == "application"
        else {"warmup", "steady_state", "burst", "recovery"}
    )
    if not required_phases <= phases:
        missing = ", ".join(sorted(required_phases - phases))
        raise EvidenceValidationError(f"workload phases missing: {missing}")

    measurements = _require_mapping(report["measurements"], "measurements")
    if profile == "application":
        p50 = _number(measurements.get("p50_ms"), "measurements.p50_ms", minimum=0)
        p95 = _number(measurements.get("p95_ms"), "measurements.p95_ms", minimum=0)
        p99 = _number(measurements.get("p99_ms"), "measurements.p99_ms", minimum=0)
        if not p50 <= p95 <= p99:
            raise EvidenceValidationError("latency percentiles must be ordered p50 <= p95 <= p99")
        _number(measurements.get("error_rate"), "measurements.error_rate", minimum=0)
        if float(measurements["error_rate"]) > 1:
            raise EvidenceValidationError("measurements.error_rate must be <= 1")
        _number(
            measurements.get("throughput_per_second"),
            "measurements.throughput_per_second",
            minimum=0,
        )
        telemetry = _require_mapping(measurements.get("telemetry"), "measurements.telemetry")
        required_telemetry = {
            "cpu",
            "memory",
            "database_pool",
            "queue",
            "redis",
            "object_store",
            "model",
        }
    else:
        for name in (
            "ttft_p50_ms",
            "ttft_p95_ms",
            "ttft_p99_ms",
            "tpot_p50_ms",
            "tpot_p95_ms",
            "tpot_p99_ms",
            "tokens_per_second",
        ):
            _number(measurements.get(name), f"measurements.{name}", minimum=0)
        _number(measurements.get("error_rate"), "measurements.error_rate", minimum=0)
        if float(measurements["error_rate"]) > 1:
            raise EvidenceValidationError("measurements.error_rate must be <= 1")
        telemetry = _require_mapping(measurements.get("telemetry"), "measurements.telemetry")
        required_telemetry = {"gpu", "gpu_memory", "kv_cache", "queue"}
    _number(measurements.get("headroom_percent"), "measurements.headroom_percent", minimum=0)
    _require_non_empty_string(measurements.get("bottleneck"), "measurements.bottleneck")
    _validate_telemetry(telemetry, required_telemetry)


def validate_evidence(report: Mapping[str, Any], *, root: Path) -> None:
    """Validate a recovery or capacity evidence report and its artifact hashes."""
    status = _validate_common(report, root)
    if report["evidence_type"] == "recovery":
        _validate_recovery(report, status)
    else:
        _validate_capacity(report, status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate recovery/capacity delivery evidence")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = _require_mapping(payload, "evidence report")
        validate_evidence(report, root=args.root)
    except (OSError, json.JSONDecodeError, EvidenceValidationError) as error:
        raise SystemExit(str(error)) from error
    print(
        json.dumps(
            {
                "validated": True,
                "evidence_id": report["evidence_id"],
                "evidence_type": report["evidence_type"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
