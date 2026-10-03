"""Validate review evidence; this command neither approves nor deploys a release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate_recovery_capacity_evidence import (
    EvidenceValidationError,
    validate_evidence,
)

REQUIRED_CHECKS = {
    "tenant_isolation": {"cross_tenant_denied", "revocation_effective", "demo_isolated"},
    "business_journey": {"github_login", "admission", "upload_ingestion", "review_export"},
    "model_quality": {
        "frozen_corpus",
        "all_attempts_included",
        "semantic_review",
        "citation_integrity",
    },
    "generation_resilience": {
        "bounded_failover",
        "refresh_recovery",
        "partial_success",
        "no_double_charge",
    },
    "commercial_metering": {
        "finite_entitlements",
        "all_exposed_model_paths",
        "usage_reconciliation",
    },
    "capacity": {"business_load", "queue_latency", "host_headroom", "approved_limits"},
    "recovery": {"independent_fault_domain", "database_and_objects", "rpo_rto", "rollback"},
    "observability": {
        "external_probe",
        "alert_delivered",
        "recovery_delivered",
        "backup_freshness",
    },
    "release_governance": {
        "protected_branch",
        "required_ci",
        "independent_approval",
        "signed_images",
    },
    "data_governance": {
        "terms_approved",
        "provider_disclosure",
        "retention_and_deletion",
        "export",
    },
    "support_acceptance": {
        "named_owner",
        "incident_runbook",
        "customer_acceptance",
        "support_channel",
    },
    "payments": {"verified_webhooks", "idempotency", "refund_cancel", "reconciliation"},
    "self_service": {"onboarding", "abuse_limits", "renewal_expiry", "tenant_exit"},
}
PUBLIC_ONLY = {"payments", "self_service"}
MAX_AGE = timedelta(days=7)
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_REPORT_BYTES = 16 * 1024 * 1024


class ReadinessError(ValueError):
    pass


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReadinessError("invalid_document")
    return value


def _path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ReadinessError("unsafe_evidence_path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ReadinessError("unsafe_evidence_path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ReadinessError("unsafe_evidence_path")
    return path


def _bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_REPORT_BYTES + 1)
    except OSError as error:
        raise ReadinessError("evidence_file_unavailable") from error
    if len(data) > MAX_REPORT_BYTES:
        raise ReadinessError("evidence_file_too_large")
    return data


def _json(data: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReadinessError("duplicate_json_key")
            result[key] = value
        return result

    try:
        return _object(json.loads(data, object_pairs_hook=unique))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ReadinessError):
            raise
        raise ReadinessError("invalid_json") from error


def _reference(root: Path, value: object) -> dict[str, Any]:
    ref = _object(value)
    digest = ref.get("sha256")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ReadinessError("invalid_evidence_digest")
    data = _bytes(_path(root, ref.get("path")))
    if hashlib.sha256(data).hexdigest() != digest:
        raise ReadinessError("evidence_digest_mismatch")
    return _json(data)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReadinessError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReadinessError("invalid_timestamp") from error
    if parsed.tzinfo is None:
        raise ReadinessError("invalid_timestamp")
    return parsed.astimezone(UTC)


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ReadinessError("missing_accountable_identity")
    return value.strip().casefold()


def _bind(report: dict[str, Any], target: dict[str, str]) -> None:
    if any(report.get(key) != value for key, value in target.items()):
        raise ReadinessError("candidate_binding_mismatch")


def _capacity_objectives(source: dict[str, Any]) -> None:
    objectives = _object(source.get("acceptance_objectives"))
    _identity(objectives.get("approved_by"))
    if _time(objectives.get("approved_at")) > _time(source.get("started_at")):
        raise ReadinessError("capacity_objectives_not_preapproved")
    for key, maximum in (
        ("max_p95_ms", math.inf),
        ("max_error_rate", 1),
        ("min_headroom_percent", 100),
    ):
        value = objectives.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= maximum
        ):
            raise ReadinessError("invalid_capacity_objectives")
    measured = _object(source.get("measurements"))
    if (
        measured["p95_ms"] > objectives["max_p95_ms"]
        or measured["error_rate"] > objectives["max_error_rate"]
        or measured["headroom_percent"] < objectives["min_headroom_percent"]
    ):
        raise ReadinessError("capacity_objective_failed")


def _gate(
    root: Path, item: dict[str, Any], gate_id: str, target: dict[str, str], now: datetime
) -> None:
    if item.get("status") != "passed":
        raise ReadinessError("gate_not_passed")
    report = _reference(root, item.get("evidence"))
    if report.get("schema_version") != 1 or report.get("gate_id") != gate_id:
        raise ReadinessError("evidence_identity_mismatch")
    if report.get("status") != "passed":
        raise ReadinessError("evidence_not_passed")
    _bind(report, target)
    completed = _time(report.get("completed_at"))
    if completed > now or now - completed > MAX_AGE:
        raise ReadinessError("evidence_not_current")
    operator = _identity(report.get("executed_by"))
    review = _object(report.get("review"))
    if review.get("status") != "approved":
        raise ReadinessError("review_not_approved")
    if _identity(review.get("reviewed_by")) == operator:
        raise ReadinessError("independent_review_required")
    reviewed = _time(review.get("reviewed_at"))
    if not completed <= reviewed <= now:
        raise ReadinessError("invalid_review_time")
    checks = _object(report.get("checks"))
    if any(checks.get(key) is not True for key in REQUIRED_CHECKS[gate_id]):
        raise ReadinessError("required_checks_not_passed")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ReadinessError("source_reports_missing")
    for reference in artifacts:
        source = _reference(root, reference)
        if source.get("status") != "passed":
            raise ReadinessError("source_report_not_passed")
        if source.get("commit_sha") != target["commit_sha"]:
            raise ReadinessError("source_commit_mismatch")
        if (
            source.get("environment") != target["environment"]
            or source.get("profile") != target["profile"]
        ):
            raise ReadinessError("source_environment_mismatch")
        source_completed = _time(source.get("completed_at"))
        if not now - MAX_AGE <= source_completed <= completed:
            raise ReadinessError("source_report_not_current")
    if gate_id in {"capacity", "recovery"}:
        source = _reference(root, report.get("measurement_report"))
        if source.get("status") != "passed" or source.get("evidence_type") != gate_id:
            raise ReadinessError("measurement_report_not_passed")
        if source.get("commit_sha") != target["commit_sha"]:
            raise ReadinessError("source_commit_mismatch")
        environment = _object(source.get("environment"))
        if (
            environment.get("profile") != target["profile"]
            or environment.get("name") != target["environment"]
        ):
            raise ReadinessError("measurement_environment_mismatch")
        if not now - MAX_AGE <= _time(source.get("completed_at")) <= completed:
            raise ReadinessError("source_report_not_current")
        if gate_id == "capacity":
            scenarios = _object(source.get("workload")).get("scenarios")
            if (
                source.get("capacity_profile") != "application"
                or not isinstance(scenarios, list)
                or not all(isinstance(value, str) for value in scenarios)
                or not {"upload", "ingestion", "retrieval", "generation_recovery"} <= set(scenarios)
            ):
                raise ReadinessError("business_capacity_required")
        try:
            validate_evidence(source, root=root)
        except EvidenceValidationError as error:
            raise ReadinessError("measurement_contract_failed") from error
        if gate_id == "capacity":
            _capacity_objectives(source)


def check(
    *,
    root: Path,
    manifest: str,
    commit: str,
    environment: str,
    profile: str,
    scope: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now(UTC)
    target = {"commit_sha": commit, "environment": environment, "profile": profile}
    issues: list[dict[str, str]] = []
    checked: list[str] = []
    try:
        if (
            not SHA.fullmatch(commit)
            or environment not in {"staging", "production"}
            or profile != "single-node-4c4g"
            or scope not in {"invited", "public_saas"}
        ):
            raise ReadinessError("invalid_release_target")
        record = _json(_bytes(_path(root, manifest)))
        if record.get("schema_version") != 1 or record.get("release_scope") != scope:
            raise ReadinessError("manifest_scope_mismatch")
        _bind(_object(record.get("candidate")), target)
        rows = record.get("gates")
        if not isinstance(rows, list):
            raise ReadinessError("invalid_gate_list")
        indexed = {}
        for row in rows:
            item = _object(row)
            gate_id = item.get("id")
            if not isinstance(gate_id, str) or gate_id not in REQUIRED_CHECKS:
                raise ReadinessError("unknown_gate")
            if gate_id in indexed:
                raise ReadinessError("duplicate_gate")
            indexed[gate_id] = item
        required = set(REQUIRED_CHECKS) - (PUBLIC_ONLY if scope == "invited" else set())
        for gate_id in sorted(required):
            try:
                if gate_id not in indexed:
                    raise ReadinessError("required_gate_missing")
                _gate(root, indexed[gate_id], gate_id, target, at)
                checked.append(gate_id)
            except ReadinessError as error:
                issues.append({"gate": gate_id, "code": str(error)})
    except ReadinessError as error:
        issues.append({"gate": "manifest", "code": str(error)})
    return {
        "schema_version": 1,
        "status": "blocked" if issues else "eligible_for_release_review",
        "release_scope": scope,
        "candidate": target,
        "checked_at": at.isoformat(),
        "validated_gates": checked,
        "issues": issues,
        "deployment_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--environment", required=True, choices=["staging", "production"])
    parser.add_argument("--profile", required=True, choices=["single-node-4c4g"])
    parser.add_argument("--scope", required=True, choices=["invited", "public_saas"])
    args = parser.parse_args()
    report = check(**vars(args))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
