from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SERVICES = {"api", "worker", "consumer", "web"}
ROLLOUT_STEPS = ("prerequisites", "migration", "workloads", "rollout")
SMOKE_STEPS = ("cluster_smoke", "authenticated_smoke")
OUTCOME_VALUES = {"success", "failure", "cancelled", "skipped"}
DEPLOYMENT_PROFILES = {"staging", "tiny-single-node"}
MODEL_PROVIDER = "openai_compatible"


class StagingReleaseRecordError(ValueError):
    """Raised when staging workflow metadata is incomplete or contradictory."""


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _normalize_model_base_url(value: str) -> str:
    if _contains_control_character(value):
        raise StagingReleaseRecordError("model base URL must not contain control characters")
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname or ""
        _ = parsed.port
    except ValueError as error:
        raise StagingReleaseRecordError("model base URL is invalid") from error
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or hostname == "localhost"
        or hostname.endswith(".localhost")
        or (address is not None and not address.is_global)
        or not parsed.path.rstrip("/").endswith("/v1")
    ):
        raise StagingReleaseRecordError(
            "model base URL must be a public HTTPS /v1 URL without credentials or query"
        )
    return normalized.rstrip("/")


def _normalize_model_name(value: str) -> str:
    if _contains_control_character(value):
        raise StagingReleaseRecordError("model name must not contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise StagingReleaseRecordError("model name must contain 1-200 characters")
    return normalized


def _model_metadata(
    *,
    model_provider: str,
    model_base_url: str,
    model_name: str,
) -> tuple[dict[str, Any], str | None]:
    try:
        if model_provider != MODEL_PROVIDER:
            raise StagingReleaseRecordError(f"model provider must be {MODEL_PROVIDER}")
        normalized_model_base_url = _normalize_model_base_url(model_base_url)
        normalized_model_name = _normalize_model_name(model_name)
    except StagingReleaseRecordError as error:
        return (
            {
                "status": "invalid",
                "provider": MODEL_PROVIDER if model_provider == MODEL_PROVIDER else None,
                "base_url": None,
                "name": None,
                "configured": {
                    "provider": bool(model_provider.strip()),
                    "base_url": bool(model_base_url.strip()),
                    "name": bool(model_name.strip()),
                },
                "validation_error": str(error),
            },
            str(error),
        )
    return (
        {
            "status": "validated",
            "provider": MODEL_PROVIDER,
            "base_url": normalized_model_base_url,
            "name": normalized_model_name,
        },
        None,
    )


def build_record(
    evidence_manifest: Path,
    *,
    deployment_profile: str,
    repository: str,
    commit_sha: str,
    run_id: str,
    run_attempt: str,
    registry_prefix: str,
    image_digests: dict[str, str],
    outcomes: dict[str, str],
    model_provider: str,
    model_base_url: str,
    model_name: str,
    smoke_required: bool,
    output: Path,
) -> dict[str, Any]:
    if deployment_profile not in DEPLOYMENT_PROFILES:
        raise StagingReleaseRecordError("deployment profile is not reviewed")
    if not evidence_manifest.is_file():
        raise StagingReleaseRecordError("sanitized evidence manifest does not exist")
    try:
        evidence = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StagingReleaseRecordError("sanitized evidence manifest is invalid JSON") from error
    if not isinstance(evidence, dict) or not evidence.get("files"):
        raise StagingReleaseRecordError("sanitized evidence manifest must list evidence files")
    if set(image_digests) != SERVICES:
        raise StagingReleaseRecordError("image digests must contain api, worker, consumer and web")
    if any(DIGEST_PATTERN.fullmatch(value) is None for value in image_digests.values()):
        raise StagingReleaseRecordError("image digests must be immutable sha256 values")
    required_outcomes = set(ROLLOUT_STEPS) | set(SMOKE_STEPS)
    if set(outcomes) != required_outcomes:
        raise StagingReleaseRecordError("workflow outcomes are incomplete")
    if any(value not in OUTCOME_VALUES for value in outcomes.values()):
        raise StagingReleaseRecordError("workflow outcomes contain an invalid value")
    model, model_error = _model_metadata(
        model_provider=model_provider,
        model_base_url=model_base_url,
        model_name=model_name,
    )

    rollout_ok = all(outcomes[name] == "success" for name in ROLLOUT_STEPS)
    smoke_ok = all(outcomes[name] == "success" for name in SMOKE_STEPS)
    if model_error is not None:
        status = "failed"
        blocking_reason = None
        failure_reason = "Model routing validation failed before staging rollout."
    elif rollout_ok and smoke_required and smoke_ok:
        status = "passed"
        blocking_reason = None
        failure_reason = None
    elif rollout_ok and not smoke_required:
        status = "blocked_external"
        blocking_reason = "Authenticated staging smoke was explicitly skipped."
        failure_reason = None
    else:
        status = "failed"
        blocking_reason = None
        failure_reason = "One or more staging rollout or smoke steps did not succeed."

    record: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "blocking_reason": blocking_reason,
        "failure_reason": failure_reason,
        "deployment_profile": deployment_profile,
        "repository": repository,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "generated_at": datetime.now(UTC).isoformat(),
        "registry_prefix": registry_prefix,
        "model": model,
        "image_digests": image_digests,
        "outcomes": outcomes,
        "evidence_manifest": {
            "path": evidence_manifest.as_posix(),
            "sha256": hashlib.sha256(evidence_manifest.read_bytes()).hexdigest(),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a staging rollout evidence record")
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--deployment-profile", choices=sorted(DEPLOYMENT_PROFILES), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--registry-prefix", required=True)
    parser.add_argument("--image-digests-json", required=True)
    parser.add_argument("--outcomes-json", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--smoke-required", choices=("true", "false"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        image_digests = json.loads(args.image_digests_json)
        outcomes = json.loads(args.outcomes_json)
        if not isinstance(image_digests, dict) or not isinstance(outcomes, dict):
            raise StagingReleaseRecordError("digest and outcome inputs must be JSON objects")
        record = build_record(
            args.evidence_manifest,
            deployment_profile=args.deployment_profile,
            repository=args.repository,
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            registry_prefix=args.registry_prefix,
            image_digests={str(key): str(value) for key, value in image_digests.items()},
            outcomes={str(key): str(value) for key, value in outcomes.items()},
            model_provider=args.model_provider,
            model_base_url=args.model_base_url,
            model_name=args.model_name,
            smoke_required=args.smoke_required == "true",
            output=args.output,
        )
    except (OSError, json.JSONDecodeError, StagingReleaseRecordError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": record["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
