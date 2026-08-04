from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_PROVIDER = "openai_compatible"
EXPECTED_DIMENSION = 1024
EXPECTED_VERSION = 2


class EmbeddingRolloutReportError(ValueError):
    """Raised when the rollout report does not prove the embedding contract."""


def _mapping(value: object, *, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EmbeddingRolloutReportError(f"{description} must be an object")
    return value


def _count(value: object, *, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EmbeddingRolloutReportError(f"{description} must be a non-negative integer")
    return value


def _validate_reindex_identity(report: dict[str, Any], *, expected_model: str) -> None:
    if (
        report.get("embedding_model") != expected_model
        or report.get("embedding_dimension") != EXPECTED_DIMENSION
        or report.get("embedding_version") != EXPECTED_VERSION
        or report.get("values_redacted") is not True
    ):
        raise EmbeddingRolloutReportError("reindex embedding identity is invalid")


def validate_embedding_rollout_report(
    path: Path,
    *,
    expected_model: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise EmbeddingRolloutReportError("embedding rollout report does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EmbeddingRolloutReportError("embedding rollout report is invalid JSON") from error
    report = _mapping(payload, description="embedding rollout report")
    if (
        report.get("schema_version") != 1
        or report.get("status") != "passed"
        or report.get("failed_stage") is not None
        or report.get("error_code") is not None
        or report.get("values_redacted") is not True
    ):
        raise EmbeddingRolloutReportError("embedding rollout did not pass")

    probe = _mapping(report.get("probe"), description="embedding probe")
    if (
        probe.get("status") != "passed"
        or probe.get("provider") != EXPECTED_PROVIDER
        or probe.get("model") != expected_model
        or probe.get("dimension") != EXPECTED_DIMENSION
        or probe.get("version") != EXPECTED_VERSION
        or probe.get("item_count") != 2
        or probe.get("finite") is not True
        or probe.get("nonzero_norms") is not True
        or probe.get("values_redacted") is not True
    ):
        raise EmbeddingRolloutReportError("embedding probe contract is invalid")

    reindex = _mapping(report.get("reindex"), description="embedding reindex")
    if reindex.get("status") != "completed":
        raise EmbeddingRolloutReportError("embedding reindex did not complete")
    initial = _mapping(reindex.get("initial_plan"), description="initial reindex plan")
    final = _mapping(reindex.get("final_plan"), description="final reindex plan")
    attempts = reindex.get("attempts")
    if not isinstance(attempts, list):
        raise EmbeddingRolloutReportError("embedding reindex attempts must be an array")

    for plan, description in ((initial, "initial plan"), (final, "final plan")):
        _validate_reindex_identity(plan, expected_model=expected_model)
        if plan.get("status") != "planned":
            raise EmbeddingRolloutReportError(f"{description} status is invalid")
        _count(plan.get("selected"), description=f"{description} selected")
        if _count(plan.get("created"), description=f"{description} created") != 0:
            raise EmbeddingRolloutReportError(f"{description} must not create jobs")
        if _count(plan.get("replayed"), description=f"{description} replayed") != 0:
            raise EmbeddingRolloutReportError(f"{description} must not replay jobs")

    created = 0
    replayed = 0
    for item in attempts:
        attempt = _mapping(item, description="reindex apply attempt")
        _validate_reindex_identity(attempt, expected_model=expected_model)
        if attempt.get("status") != "applied":
            raise EmbeddingRolloutReportError("reindex apply attempt status is invalid")
        selected = _count(attempt.get("selected"), description="apply selected")
        attempt_created = _count(attempt.get("created"), description="apply created")
        attempt_replayed = _count(attempt.get("replayed"), description="apply replayed")
        if selected != attempt_created + attempt_replayed:
            raise EmbeddingRolloutReportError("reindex apply counts are inconsistent")
        created += attempt_created
        replayed += attempt_replayed

    initial_selected = _count(initial.get("selected"), description="initial selected")
    final_selected = _count(final.get("selected"), description="final selected")
    if final_selected != 0:
        raise EmbeddingRolloutReportError("embedding reindex did not converge")

    return {
        "status": "validated",
        "provider": EXPECTED_PROVIDER,
        "model": expected_model,
        "dimension": EXPECTED_DIMENSION,
        "version": EXPECTED_VERSION,
        "probe_item_count": 2,
        "reindex": {
            "status": "completed",
            "initial_selected": initial_selected,
            "apply_attempts": len(attempts),
            "created": created,
            "replayed": replayed,
            "final_selected": final_selected,
        },
        "values_redacted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an embedding rollout JSON report")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-model", required=True)
    args = parser.parse_args()
    try:
        summary = validate_embedding_rollout_report(
            args.input,
            expected_model=args.expected_model,
        )
    except EmbeddingRolloutReportError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
