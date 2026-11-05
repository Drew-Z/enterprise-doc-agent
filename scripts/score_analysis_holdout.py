"""Score an analysis execution report against a separately reviewed reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class AnalysisScoreError(ValueError):
    """Raised when execution or reference identities do not match."""


def _read_json(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise AnalysisScoreError("score_input_invalid")
    try:
        value = json.loads(path.read_bytes())
    except json.JSONDecodeError:
        raise AnalysisScoreError("score_input_invalid") from None
    if not isinstance(value, dict):
        raise AnalysisScoreError("score_input_invalid")
    return value


def _row_key(row: object) -> tuple[str, str] | None:
    if not isinstance(row, dict):
        return None
    object_id = row.get("object_id")
    field_id = row.get("field_id")
    if not isinstance(object_id, str) or not isinstance(field_id, str):
        return None
    return object_id, field_id


def _substantive_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if actual.get("status") != "succeeded" or not isinstance(actual.get("result"), dict):
        return False
    result = actual["result"]
    for name in ("action", "evidence_state", "value", "conditions"):
        if name in expected and result.get(name) != expected[name]:
            return False
    return True


def _evidence_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not _substantive_match(actual, expected):
        return False
    result = actual["result"]
    required_quotes = expected.get("required_quote_sha256", [])
    if not isinstance(required_quotes, list):
        return False
    observed = {
        hashlib.sha256(str(item.get("quote", "")).encode("utf-8")).hexdigest()
        for item in result.get("evidence", [])
        if isinstance(item, dict)
    }
    return all(isinstance(item, str) and item in observed for item in required_quotes)


def _serious_error(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Flag unsafe assertions, wrong facts, or erased conflicts; omissions stay revisions."""
    if actual.get("status") != "succeeded" or not isinstance(actual.get("result"), dict):
        return False
    result = actual["result"]
    expected_action = expected.get("action")
    expected_state = expected.get("evidence_state")
    actual_action = result.get("action")
    actual_state = result.get("evidence_state")
    if expected_action != "final" and actual_action == "final":
        return True
    if expected_state != "supported" and actual_state == "supported":
        return True
    if expected_state == "conflicting" and actual_state != "conflicting":
        return True
    if expected_action == actual_action == "final" and result.get("value") != expected.get("value"):
        return True
    return bool(
        expected_action == actual_action == "final"
        and "conditions" in expected
        and result.get("conditions") != expected["conditions"]
    )


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _operational(arm: object) -> dict[str, object]:
    if not isinstance(arm, dict):
        arm = {}
    usage = arm.get("usage") if isinstance(arm.get("usage"), dict) else {}
    latency = arm.get("latency_ms")
    cost = _decimal(usage.get("estimated_cost"))
    return {
        "latency_ms": (
            float(latency)
            if isinstance(latency, (int, float))
            and not isinstance(latency, bool)
            and math.isfinite(float(latency))
            and latency >= 0
            else None
        ),
        "dispatches": usage.get("dispatches")
        if isinstance(usage.get("dispatches"), int)
        and not isinstance(usage.get("dispatches"), bool)
        else None,
        "known_total_tokens": usage.get("known_total_tokens")
        if isinstance(usage.get("known_total_tokens"), int)
        and not isinstance(usage.get("known_total_tokens"), bool)
        else None,
        "unknown_token_dispatches": usage.get("unknown_token_dispatches")
        if isinstance(usage.get("unknown_token_dispatches"), int)
        and not isinstance(usage.get("unknown_token_dispatches"), bool)
        else None,
        "estimated_cost": str(cost) if cost is not None else None,
        "currency": usage.get("currency") if isinstance(usage.get("currency"), str) else None,
        "cost_status": "known" if cost is not None else "unknown",
    }


def _delta(left: object, right: object) -> float | int | None:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return left - right
    return None


def _operational_comparison(quality: dict[str, Any]) -> dict[str, object]:
    fixed = quality["fixed"]["operational"]
    agent = quality["agent"]["operational"]
    fixed_cost = _decimal(fixed["estimated_cost"])
    agent_cost = _decimal(agent["estimated_cost"])
    currency = agent["currency"] if agent["currency"] == fixed["currency"] else None
    cost_delta = (
        agent_cost - fixed_cost
        if currency and agent_cost is not None and fixed_cost is not None
        else None
    )
    token_delta = (
        _delta(agent["known_total_tokens"], fixed["known_total_tokens"])
        if agent["unknown_token_dispatches"] == fixed["unknown_token_dispatches"] == 0
        else None
    )
    return {
        "substantive_revision_rows_delta_agent_minus_fixed": (
            quality["agent"]["substantive_revision_rows"]
            - quality["fixed"]["substantive_revision_rows"]
        ),
        "latency_ms_delta_agent_minus_fixed": _delta(agent["latency_ms"], fixed["latency_ms"]),
        "dispatches_delta_agent_minus_fixed": _delta(agent["dispatches"], fixed["dispatches"]),
        "known_total_tokens_delta_agent_minus_fixed": token_delta,
        "estimated_cost_delta_agent_minus_fixed": (
            str(cost_delta) if cost_delta is not None else None
        ),
        "currency": currency,
    }


def score_analysis_holdout(*, execution_path: Path, reference_path: Path) -> dict[str, object]:
    execution = _read_json(execution_path)
    reference = _read_json(reference_path, max_bytes=2 * 1024 * 1024)
    if execution.get("schema_version") != "analysis.holdout-execution.v1":
        raise AnalysisScoreError("execution_schema_invalid")
    boundary = execution.get("reference_boundary")
    if not isinstance(boundary, dict):
        raise AnalysisScoreError("execution_reference_identity_missing")
    reference_bytes = reference_path.read_bytes()
    if (
        boundary.get("bytes") != len(reference_bytes)
        or boundary.get("sha256") != hashlib.sha256(reference_bytes).hexdigest()
    ):
        raise AnalysisScoreError("execution_reference_identity_mismatch")
    rows = reference.get("rows")
    denominator = reference.get("denominator")
    annotated = reference.get("annotated_rows")
    if (
        isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
        or not isinstance(rows, list)
        or len(rows) != denominator
        or isinstance(annotated, bool)
        or not isinstance(annotated, int)
        or not 0 <= annotated <= denominator
    ):
        raise AnalysisScoreError("reference_denominator_invalid")
    reference_keys = [_row_key(row) for row in rows]
    if None in reference_keys or len(set(reference_keys)) != denominator:
        raise AnalysisScoreError("reference_rows_invalid")
    if execution.get("planned_denominator") != denominator * 2:
        raise AnalysisScoreError("execution_reference_denominator_mismatch")
    independently_reviewed = reference.get("independently_reviewed") is True
    fully_reviewed = (
        independently_reviewed
        and annotated == denominator
        and all(
            isinstance(row, dict)
            and row.get("annotation_status") == "reviewed"
            and isinstance(row.get("expected_result"), dict)
            for row in rows
        )
    )
    base: dict[str, object] = {
        "schema_version": "analysis.holdout-score.v1",
        "execution_sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
        "reference_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "pack_id": execution.get("pack_id"),
        "reference_denominator": denominator,
        "annotated_rows": annotated,
        "unscored_rows": denominator - annotated,
        "independently_reviewed": independently_reviewed,
    }
    if not fully_reviewed:
        return {
            **base,
            "status": "blocked_reference_unreviewed",
            "quality": {"fixed": None, "agent": None, "delta": None},
            "limitations": [
                "All reference rows require independent review before quality scoring.",
                "Execution failures and not-run rows remain in the denominator after review.",
            ],
        }

    reference_map = {_row_key(row): row["expected_result"] for row in rows if isinstance(row, dict)}
    preservation_keys = {
        key
        for key, expected in reference_map.items()
        if expected.get("evidence_state") in {"conflicting", "insufficient", "unreadable"}
    }
    quality: dict[str, Any] = {}
    for mode in ("fixed", "agent"):
        arm = execution.get("arms", {}).get(mode, {})
        actual_rows = arm.get("rows", []) if isinstance(arm, dict) else []
        actual_map = {_row_key(row): row for row in actual_rows if isinstance(row, dict)}
        substantive = sum(
            _substantive_match(actual_map.get(key, {}), expected)
            for key, expected in reference_map.items()
        )
        evidence_matches = sum(
            _evidence_match(actual_map.get(key, {}), expected)
            for key, expected in reference_map.items()
        )
        serious = sum(
            _serious_error(actual_map.get(key, {}), expected)
            for key, expected in reference_map.items()
        )
        preserved = sum(
            actual_map.get(key, {}).get("status") == "succeeded"
            and isinstance(actual_map.get(key, {}).get("result"), dict)
            and actual_map[key]["result"].get("evidence_state")
            == reference_map[key].get("evidence_state")
            for key in preservation_keys
        )
        retention_rate = preserved / len(preservation_keys) if preservation_keys else None
        thresholds = {
            "no_substantive_revision_rate_at_least_0_80": substantive / denominator >= 0.8,
            "serious_error_rows_equal_0": serious == 0,
            "missing_conflict_retention_rate_at_least_0_90": (
                retention_rate is None or retention_rate >= 0.9
            ),
        }
        quality[mode] = {
            "denominator": denominator,
            "no_substantive_revision_rows": substantive,
            "no_substantive_revision_rate": substantive / denominator,
            "substantive_revision_rows": denominator - substantive,
            "serious_error_rows": serious,
            "reference_match_rows": evidence_matches,
            "reference_match_rate": evidence_matches / denominator,
            "missing_conflict_preservation": {
                "preserved_rows": preserved,
                "denominator": len(preservation_keys),
                "retention_rate": retention_rate,
            },
            "failed_or_not_run_rows": sum(
                actual_map.get(key, {}).get("status") != "succeeded" for key in reference_map
            ),
            "operational": _operational(arm),
            "thresholds": thresholds,
            "thresholds_passed": all(thresholds.values()),
        }
    comparison = _operational_comparison(quality)
    agent_passed = quality["agent"]["thresholds_passed"]
    return {
        **base,
        "status": (
            "passed_quality_thresholds_requires_human_acceptance"
            if agent_passed
            else "failed_quality_thresholds"
        ),
        "quality": quality,
        "comparison": comparison,
        "acceptance": {
            "evaluated_mode": "agent",
            "thresholds_passed": agent_passed,
            "human_acceptance_required": True,
        },
        "limitations": [
            (
                "Serious-error rules are deterministic projections of an independently "
                "reviewed reference; final semantic acceptance remains human."
            ),
            "A single execution does not establish repeatability.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = score_analysis_holdout(
            execution_path=args.execution, reference_path=args.reference
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (AnalysisScoreError, OSError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    if result["status"].startswith("blocked"):
        raise SystemExit(2)
    if result["status"].startswith("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
