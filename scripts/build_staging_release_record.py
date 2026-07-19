from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SERVICES = {"api", "worker", "consumer", "web"}
ROLLOUT_STEPS = ("prerequisites", "migration", "workloads", "rollout")
SMOKE_STEPS = ("cluster_smoke", "authenticated_smoke")
OUTCOME_VALUES = {"success", "failure", "cancelled", "skipped"}


class StagingReleaseRecordError(ValueError):
    """Raised when staging workflow metadata is incomplete or contradictory."""


def build_record(
    evidence_manifest: Path,
    *,
    repository: str,
    commit_sha: str,
    run_id: str,
    run_attempt: str,
    registry_prefix: str,
    image_digests: dict[str, str],
    outcomes: dict[str, str],
    smoke_required: bool,
    output: Path,
) -> dict[str, Any]:
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

    rollout_ok = all(outcomes[name] == "success" for name in ROLLOUT_STEPS)
    smoke_ok = all(outcomes[name] == "success" for name in SMOKE_STEPS)
    if rollout_ok and smoke_required and smoke_ok:
        status = "passed"
        blocking_reason = None
    elif rollout_ok and not smoke_required:
        status = "blocked_external"
        blocking_reason = "Authenticated staging smoke was explicitly skipped."
    else:
        status = "failed"
        blocking_reason = None

    record: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "blocking_reason": blocking_reason,
        "repository": repository,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "generated_at": datetime.now(UTC).isoformat(),
        "registry_prefix": registry_prefix,
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
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--registry-prefix", required=True)
    parser.add_argument("--image-digests-json", required=True)
    parser.add_argument("--outcomes-json", required=True)
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
            repository=args.repository,
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            registry_prefix=args.registry_prefix,
            image_digests={str(key): str(value) for key, value in image_digests.items()},
            outcomes={str(key): str(value) for key, value in outcomes.items()},
            smoke_required=args.smoke_required == "true",
            output=args.output,
        )
    except (OSError, json.JSONDecodeError, StagingReleaseRecordError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": record["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
