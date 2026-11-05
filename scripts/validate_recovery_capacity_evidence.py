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
QUANTITY_PATTERN = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$")
ANALYSIS_RELEASE_SERVICES = {"api", "worker", "consumer", "web"}


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


def _artifact_by_kind(report: Mapping[str, Any], root: Path, kind: str) -> Path:
    matches = [
        _artifact_path(root, item.get("path"))
        for item in report.get("artifacts", [])
        if isinstance(item, Mapping) and item.get("kind") == kind
    ]
    if len(matches) != 1:
        raise EvidenceValidationError(f"exactly one {kind} artifact is required")
    return matches[0]


def _json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except json.JSONDecodeError:
        raise EvidenceValidationError(f"{label} artifact must contain JSON") from None
    return _require_mapping(value, label)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _resource_quantity(value: object) -> float:
    if not isinstance(value, str):
        raise EvidenceValidationError("resource quantity must be a string")
    match = QUANTITY_PATTERN.fullmatch(value)
    if match is None:
        raise EvidenceValidationError("resource quantity is invalid")
    scale = {
        "": 1,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    result = float(match.group(1)) * scale[match.group(2) or ""]
    if not math.isfinite(result):
        raise EvidenceValidationError("resource quantity is invalid")
    return result


def _snapshot_identity(snapshot: Mapping[str, Any]) -> str:
    inventory = _require_mapping(snapshot.get("inventory"), "resource snapshot inventory")
    deployments = inventory.get("deployments")
    pods = inventory.get("pods")
    if (
        not isinstance(deployments, Sequence)
        or isinstance(deployments, (str, bytes))
        or not isinstance(pods, Sequence)
        or isinstance(pods, (str, bytes))
    ):
        raise EvidenceValidationError("resource snapshot identity is incomplete")
    return _canonical_sha256(
        {
            "namespace": snapshot.get("namespace_sha256"),
            "cluster": inventory.get("cluster_sha256"),
            "configuration": inventory.get("configuration_sha256"),
            "nodes": inventory.get("nodes"),
            "deployments": [
                {
                    key: item.get(key)
                    for key in (
                        "role",
                        "uid_sha256",
                        "template_sha256",
                        "containers",
                        "desired_replicas",
                    )
                }
                for item in deployments
                if isinstance(item, Mapping)
            ],
            "image_relationships": inventory.get("image_relationships", []),
            "pods": [
                {
                    "role": item.get("role"),
                    "uid": item.get("uid_sha256"),
                    "containers": item.get("containers"),
                }
                for item in pods
                if isinstance(item, Mapping)
            ],
        }
    )


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
        sample_count = value.get("sample_count")
        if (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, (int, float))
            or sample_count <= 0
        ):
            raise EvidenceValidationError(
                f"measurements.telemetry.{name}.sample_count must be positive"
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
        objectives = _require_mapping(report.get("recovery_objectives"), "recovery_objectives")
        objective_rpo = _number(
            objectives.get("rpo_seconds"),
            "recovery_objectives.rpo_seconds",
            minimum=0,
        )
        objective_rto = _number(
            objectives.get("rto_seconds"),
            "recovery_objectives.rto_seconds",
            minimum=0,
        )
        _require_non_empty_string(objectives.get("approved_by"), "recovery_objectives.approved_by")
        approved_at = _timestamp(objectives.get("approved_at"), "recovery_objectives.approved_at")
        started_at = _timestamp(report.get("started_at"), "started_at")
        if approved_at > started_at:
            raise EvidenceValidationError(
                "recovery objectives must be approved before the drill starts"
            )

        timeline = _require_mapping(report.get("recovery_timeline"), "recovery_timeline")
        latest_recoverable = _timestamp(
            timeline.get("latest_recoverable_data_at"),
            "recovery_timeline.latest_recoverable_data_at",
        )
        failure_declared = _timestamp(
            timeline.get("failure_declared_at"),
            "recovery_timeline.failure_declared_at",
        )
        application_restored = _timestamp(
            timeline.get("application_restored_at"),
            "recovery_timeline.application_restored_at",
        )
        if latest_recoverable > failure_declared:
            raise EvidenceValidationError(
                "latest recoverable data must not follow failure declaration"
            )
        if application_restored < failure_declared:
            raise EvidenceValidationError(
                "application restoration must not precede failure declaration"
            )
        calculated_rpo = (failure_declared - latest_recoverable).total_seconds()
        calculated_rto = (application_restored - failure_declared).total_seconds()
        measured_rpo = float(measurements["rpo_seconds"])
        measured_rto = float(measurements["rto_seconds"])
        if not math.isclose(measured_rpo, calculated_rpo, abs_tol=0.001):
            raise EvidenceValidationError("measurements.rpo_seconds must equal the calculated RPO")
        if not math.isclose(measured_rto, calculated_rto, abs_tol=0.001):
            raise EvidenceValidationError("measurements.rto_seconds must equal the calculated RTO")
        if measured_rpo > objective_rpo:
            raise EvidenceValidationError("measured RPO exceeds the approved RPO objective")
        if measured_rto > objective_rto:
            raise EvidenceValidationError("measured RTO exceeds the approved RTO objective")

        review = _require_mapping(report.get("review"), "review")
        reviewer = _require_non_empty_string(review.get("reviewed_by"), "review.reviewed_by")
        operator = _require_non_empty_string(report.get("operator"), "operator")
        if reviewer == operator:
            raise EvidenceValidationError("recovery evidence requires an independent reviewer")
        reviewed_at = _timestamp(review.get("reviewed_at"), "review.reviewed_at")
        completed_at = _timestamp(report.get("completed_at"), "completed_at")
        if reviewed_at < completed_at:
            raise EvidenceValidationError("recovery review must occur after completion")
        if review.get("production_like") is not True:
            raise EvidenceValidationError("review.production_like must be true")

        recovery_scope = _require_mapping(report.get("recovery_scope"), "recovery_scope")
        source_environment = _require_non_empty_string(
            recovery_scope.get("source_environment"),
            "recovery_scope.source_environment",
        )
        recovery_environment = _require_non_empty_string(
            recovery_scope.get("recovery_environment"),
            "recovery_scope.recovery_environment",
        )
        if source_environment == recovery_environment:
            raise EvidenceValidationError(
                "recovery_scope source and recovery environments must differ"
            )
        if recovery_scope.get("fault_domain_isolation_verified") is not True:
            raise EvidenceValidationError(
                "recovery_scope.fault_domain_isolation_verified must be true"
            )
        if recovery_scope.get("live_environment_mutated") is not False:
            raise EvidenceValidationError(
                "production-like recovery must not mutate the live environment"
            )

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


def _validate_analysis_4c4g_artifacts(
    report: Mapping[str, Any], resource: Mapping[str, Any], *, root: Path
) -> None:
    resource_payload = _json_object(
        _artifact_by_kind(report, root, "analysis-capacity-resources"),
        "analysis capacity resources",
    )
    snapshots_value = resource_payload.get("snapshots")
    if (
        not isinstance(snapshots_value, Sequence)
        or isinstance(snapshots_value, (str, bytes))
        or len(snapshots_value) < 2
        or not all(isinstance(item, Mapping) for item in snapshots_value)
    ):
        raise EvidenceValidationError("analysis-4c4g resource artifact snapshots are incomplete")
    snapshots = list(snapshots_value)
    captured = [
        _timestamp(item.get("captured_at"), "resource snapshot captured_at") for item in snapshots
    ]
    if (
        resource.get("sample_count") != len(snapshots)
        or captured != sorted(captured)
        or snapshots[0].get("captured_at") != resource.get("first_captured_at")
        or snapshots[-1].get("captured_at") != resource.get("last_captured_at")
    ):
        raise EvidenceValidationError("analysis-4c4g resource artifact window is inconsistent")
    if _canonical_sha256(snapshots) != resource.get("samples_sha256"):
        raise EvidenceValidationError("analysis-4c4g resource artifact digest is inconsistent")
    identities = {_snapshot_identity(item) for item in snapshots}
    if len(identities) != 1 or next(iter(identities)) != resource.get("identity_sha256"):
        raise EvidenceValidationError("analysis-4c4g resource artifact identity is inconsistent")

    release_payload = _json_object(
        _artifact_by_kind(report, root, "analysis-capacity-release-manifest"),
        "analysis capacity release manifest",
    )
    release = _require_mapping(release_payload.get("release"), "release manifest release")
    release_fields = ("repository", "ref", "run_id", "run_attempt", "workflow_ref")
    release_values = {
        field: _require_non_empty_string(release.get(field), f"release manifest release.{field}")
        for field in release_fields
    }
    images = _require_mapping(release_payload.get("images"), "release manifest images")
    release_identity = _require_mapping(report.get("release_identity"), "release_identity")
    identity_values = {
        field: _require_non_empty_string(release_identity.get(field), f"release_identity.{field}")
        for field in release_fields
    }
    artifact_images = {
        service: item.get("digest")
        for service, item in images.items()
        if isinstance(service, str) and isinstance(item, Mapping)
    }
    if (
        release_payload.get("schema_version") != 1
        or release_payload.get("status") != "passed"
        or release.get("commit_sha") != report.get("commit_sha")
        or release_values != identity_values
        or release_identity.get("sha256")
        != hashlib.sha256(
            _artifact_by_kind(report, root, "analysis-capacity-release-manifest").read_bytes()
        ).hexdigest()
        or set(artifact_images) != ANALYSIS_RELEASE_SERVICES
        or artifact_images != report.get("image_digest")
    ):
        raise EvidenceValidationError("analysis-4c4g release artifact identity is inconsistent")

    binding = _require_mapping(resource.get("target_binding"), "resource target_binding")
    expected_images = _require_mapping(report.get("image_digest"), "image_digest")
    expected_count = binding.get("expected_http_requests")
    expected_marker = binding.get("correlation_sha256")
    expected_pod = binding.get("api_pod_uid_sha256")
    counts: list[int] = []
    for snapshot in snapshots:
        if snapshot.get("errors") != []:
            raise EvidenceValidationError("analysis-4c4g resource artifact source is unavailable")
        inventory = _require_mapping(snapshot.get("inventory"), "resource snapshot inventory")
        host = _require_mapping(snapshot.get("host"), "resource snapshot host")
        pods = inventory.get("pods")
        deployments = inventory.get("deployments")
        if (
            not isinstance(pods, Sequence)
            or isinstance(pods, (str, bytes))
            or not isinstance(deployments, Sequence)
            or isinstance(deployments, (str, bytes))
        ):
            raise EvidenceValidationError("analysis-4c4g resource artifact pods are invalid")
        api_pods = [
            item for item in pods if isinstance(item, Mapping) and item.get("role") == "api"
        ]
        proof = _require_mapping(snapshot.get("traffic_proof"), "resource traffic_proof")
        count = proof.get("observations")
        observed_images: dict[str, str] = {}
        ready_roles: set[str] = set()
        for pod in pods:
            if not isinstance(pod, Mapping) or not isinstance(pod.get("containers"), Sequence):
                continue
            role = pod.get("role")
            containers = pod["containers"]
            if (
                role in expected_images
                and len(containers) == 1
                and isinstance(containers[0], Mapping)
                and isinstance(containers[0].get("running_digest"), str)
            ):
                observed_images[str(role)] = str(containers[0]["running_digest"])
            if (
                pod.get("phase") == "Running"
                and containers
                and all(
                    isinstance(container, Mapping)
                    and container.get("ready") is True
                    and container.get("last_oom") is False
                    for container in containers
                )
            ):
                ready_roles.add(str(role))
        deployment_roles = {
            str(item.get("role"))
            for item in deployments
            if isinstance(item, Mapping)
            and item.get("desired_replicas") == 1
            and item.get("ready_replicas") == 1
            and item.get("updated_replicas") == 1
            and item.get("generation") == item.get("observed_generation")
        }
        nodes = inventory.get("nodes")
        relationships = {
            (item.get("configured_digest"), item.get("running_digest"))
            for item in inventory.get("image_relationships", [])
            if isinstance(item, Mapping) and item.get("verified") is True
        }
        images_match = all(
            observed_images.get(service) == digest
            or (digest, observed_images.get(service)) in relationships
            for service, digest in expected_images.items()
        )
        try:
            node_cpu = _resource_quantity(nodes[0].get("cpu_capacity"))  # type: ignore[union-attr]
            node_memory = _resource_quantity(nodes[0].get("memory_capacity"))  # type: ignore[union-attr]
        except (IndexError, TypeError, AttributeError, EvidenceValidationError):
            node_cpu = None
            node_memory = None
        if (
            host.get("cpu_count") != 4
            or not isinstance(host.get("memory_total_bytes"), int)
            or abs(host["memory_total_bytes"] - 4 * 1024**3) > 512 * 1024**2
            or not isinstance(nodes, Sequence)
            or isinstance(nodes, (str, bytes))
            or len(nodes) != 1
            or not isinstance(nodes[0], Mapping)
            or node_cpu != 4
            or node_memory is None
            or abs(node_memory - 4 * 1024**3) > 512 * 1024**2
            or not set(expected_images) <= ready_roles
            or not set(expected_images) <= deployment_roles
            or not images_match
            or len(api_pods) != 1
            or api_pods[0].get("uid_sha256") != expected_pod
            or proof.get("api_pod_uid_sha256") != expected_pod
            or proof.get("correlation_sha256") != expected_marker
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or proof.get("status") != ("observed" if count else "not_observed")
        ):
            raise EvidenceValidationError(
                "analysis-4c4g resource artifact target binding is invalid"
            )
        counts.append(count)
    if counts != sorted(counts) or counts[0] != 0 or counts[-1] != expected_count:
        raise EvidenceValidationError(
            "analysis-4c4g resource artifact target binding is incomplete"
        )


def _validate_capacity(report: Mapping[str, Any], status: str, *, root: Path) -> None:
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

    if workload.get("name") == "analysis-4c4g":
        targets = _require_mapping(report.get("targets"), "targets")
        if targets.get("cpu_cores") != 4 or targets.get("memory_bytes") != 4 * 1024**3:
            raise EvidenceValidationError("analysis-4c4g targets must be exactly 4 CPU and 4 GiB")
        environment = _require_mapping(report.get("environment"), "environment")
        if environment.get("resource_evidence_status") != "verified":
            raise EvidenceValidationError("analysis-4c4g resource evidence must be verified")
        if environment.get("target_binding_status") != "verified":
            raise EvidenceValidationError("analysis-4c4g target binding must be verified")
        release = _require_mapping(report.get("release_identity"), "release_identity")
        if release.get("commit_sha") != report.get("commit_sha"):
            raise EvidenceValidationError("analysis-4c4g release commit must match report")
        release_images = _require_mapping(release.get("image_digests"), "release image_digests")
        if release_images != report.get("image_digest"):
            raise EvidenceValidationError("analysis-4c4g release images must match report")
        resource = _require_mapping(report.get("resource_evidence"), "resource_evidence")
        if (
            resource.get("status") != "verified"
            or resource.get("cpu_cores") != 4
            or resource.get("memory_bytes") != 4 * 1024**3
            or resource.get("image_digests") != report.get("image_digest")
            or isinstance(resource.get("sample_count"), bool)
            or not isinstance(resource.get("sample_count"), int)
            or resource.get("sample_count", 0) < 2
        ):
            raise EvidenceValidationError("analysis-4c4g resource evidence is incomplete")
        for field in ("identity_sha256", "samples_sha256"):
            value = resource.get(field)
            if not isinstance(value, str) or ARTIFACT_SHA_PATTERN.fullmatch(value) is None:
                raise EvidenceValidationError(f"resource_evidence.{field} must be SHA-256")
        binding = _require_mapping(resource.get("target_binding"), "resource target_binding")
        workload_origin = _require_non_empty_string(workload.get("base_url"), "workload.base_url")
        observations = binding.get("observations")
        expected_http_requests = binding.get("expected_http_requests")
        if (
            binding.get("status") != "verified"
            or binding.get("origin") != workload_origin
            or isinstance(observations, bool)
            or not isinstance(observations, int)
            or observations < 1
            or isinstance(expected_http_requests, bool)
            or not isinstance(expected_http_requests, int)
            or expected_http_requests != observations
            or report.get("http_requests") != expected_http_requests
        ):
            raise EvidenceValidationError("analysis-4c4g target binding is incomplete")
        for field in ("correlation_sha256", "api_pod_uid_sha256"):
            value = binding.get(field)
            if not isinstance(value, str) or ARTIFACT_SHA_PATTERN.fullmatch(value) is None:
                raise EvidenceValidationError(f"resource target_binding.{field} must be SHA-256")
        first = _timestamp(resource.get("first_captured_at"), "resource_evidence.first_captured_at")
        last = _timestamp(resource.get("last_captured_at"), "resource_evidence.last_captured_at")
        started = _timestamp(report.get("started_at"), "started_at")
        completed = _timestamp(report.get("completed_at"), "completed_at")
        if first > started or last < completed:
            raise EvidenceValidationError("analysis-4c4g resource samples must cover workload")
        kinds = {
            item.get("kind") for item in report.get("artifacts", []) if isinstance(item, Mapping)
        }
        if (
            not {
                "analysis-capacity-resources",
                "analysis-capacity-release-manifest",
            }
            <= kinds
        ):
            raise EvidenceValidationError("analysis-4c4g machine evidence artifacts are missing")
        _validate_analysis_4c4g_artifacts(report, resource, root=root)

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
        _validate_capacity(report, status, root=root)


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
