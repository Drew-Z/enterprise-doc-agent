"""Offline prerequisite checks using source-bound expectations and an explicit review map.

The reviewer maps business meanings to original output indexes. This tool never
infers that mapping from prose, repairs outputs, or turns a failed call into a draft.
Passing these checks is not an independent semantic or production acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from enterprise_doc_core.presales.citation_selection import resolve_selection
from enterprise_doc_core.presales.schemas import GenerationInput, PresalesModel, TextItem
from scripts.evaluate_presales_quality import Anchor, load_dataset, write_json
from scripts.score_presales_gateway import bind_projected_input, score

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Index = Annotated[int, Field(strict=True, ge=0, lt=12)]


class ExpectedPrerequisite(PresalesModel):
    key: Key
    description: TextItem
    state: Literal["met", "unmet", "unknown"]
    required_evidence: list[Anchor] = Field(min_length=1, max_length=12)


class ExpectedRow(PresalesModel):
    key: Key
    prerequisites: list[ExpectedPrerequisite] = Field(max_length=12)


class PrerequisiteGold(PresalesModel):
    schema_version: Literal["presales-prerequisite-gold-v1"]
    dataset_sha256: Digest
    review_status: TextItem
    rows: list[ExpectedRow] = Field(min_length=1, max_length=6)


class Mapping(PresalesModel):
    expected_key: Key
    observed_index: Index | None
    reason: TextItem


class ReviewRow(PresalesModel):
    key: Key
    reviewed: bool = Field(strict=True)
    mappings: list[Mapping] = Field(max_length=12)


class PrerequisiteReview(PresalesModel):
    schema_version: Literal["presales-prerequisite-review-v1"]
    run_sha256: Digest
    expectations_sha256: Digest
    reviewer: TextItem
    review_type: Literal["assistant", "human"]
    rows: list[ReviewRow] = Field(max_length=6)


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("review_input_too_large")
    return raw


def score_prerequisites(
    dataset_path: Path,
    gold_path: Path,
    run_path: Path,
    expectations_path: Path,
    review_path: Path,
) -> dict[str, Any]:
    run_raw = read_bounded(run_path, 2 * 1024 * 1024)
    expected_raw = read_bounded(expectations_path, 256 * 1024)
    expected = PrerequisiteGold.model_validate_json(expected_raw)
    review_raw = read_bounded(review_path, 256 * 1024)
    review = PrerequisiteReview.model_validate_json(review_raw)
    run_hash = hashlib.sha256(run_raw).hexdigest()
    expected_hash = hashlib.sha256(expected_raw).hexdigest()
    if review.run_sha256 != run_hash or review.expectations_sha256 != expected_hash:
        raise ValueError("review_binding_mismatch")
    report = json.loads(run_raw)
    if report["schemaVersion"] not in {"presales-gateway-run-v2", "presales-gateway-run-v3"}:
        raise ValueError("prerequisite_report_scope_unsupported")
    baseline = score(dataset_path, gold_path, report)
    dataset, digest = load_dataset(dataset_path)
    if expected.dataset_sha256 != digest:
        raise ValueError("prerequisite_dataset_mismatch")
    targets = {row.key: row for row in expected.rows}
    requirements = {row.key for row in dataset.requirements}
    reviews = {row.key: row for row in review.rows}
    if len(targets) != len(expected.rows) or set(targets) != requirements:
        raise ValueError("prerequisite_gold_coverage_mismatch")
    if len(reviews) != len(review.rows) or not set(reviews) <= requirements:
        raise ValueError("prerequisite_review_coverage_mismatch")
    sources = {source.key: source for source in dataset.sources}
    reverse = {value: key for key, value in report["versions"].items()}
    observations = {row["key"]: row for row in report["observations"]}
    baseline_rows = {row["key"]: row for row in baseline["rows"]}
    rows: list[dict[str, Any]] = []
    for key, target in targets.items():
        expected_keys = {item.key for item in target.prerequisites}
        if len(expected_keys) != len(target.prerequisites):
            raise ValueError("duplicate_expected_prerequisite")
        for expected_item in target.prerequisites:
            if any(
                anchor.source_key not in sources
                or anchor.excerpt not in sources[anchor.source_key].content
                for anchor in expected_item.required_evidence
            ):
                raise ValueError("invalid_prerequisite_gold_anchor")
        observation = observations.get(key, {})
        observable = observation.get("state") == "succeeded"
        mapped = reviews.get(key)
        reviewed = mapped is not None and mapped.reviewed
        mappings = {item.expected_key: item for item in mapped.mappings} if mapped else {}
        if mapped and (
            len(mappings) != len(mapped.mappings)
            or not set(mappings) <= expected_keys
            or (reviewed and set(mappings) != expected_keys)
        ):
            raise ValueError("prerequisite_mapping_coverage_mismatch")
        indexes = [
            item.observed_index for item in mappings.values() if item.observed_index is not None
        ]
        if len(set(indexes)) != len(indexes):
            raise ValueError("duplicate_prerequisite_mapping")
        if not observable and (reviewed or indexes):
            raise ValueError("cannot_review_failed_or_unattempted_output")
        draft = None
        actual = []
        if observable:
            trace = observation["traces"][0]
            catalog = bind_projected_input(
                trace["input"], GenerationInput.model_validate(observation["sourceInput"])
            )
            # Baseline scoring has already bound this accepted output to its result.
            # Historical v2 states are read for a new analysis, never written back.
            draft = resolve_selection(
                trace["response"]["choices"][0]["message"]["content"], catalog
            )
            actual = draft.prerequisites or []
        if any(index >= len(actual) for index in indexes):
            raise ValueError("prerequisite_mapping_index_out_of_range")
        items: list[dict[str, Any]] = []
        for target_item in target.prerequisites:
            mapping = mappings.get(target_item.key)
            index = mapping.observed_index if reviewed and mapping else None
            item = actual[index] if index is not None else None
            citations = (
                [draft.citations[i] for i in item.citation_indexes] if item and draft else []
            )
            covered = sum(
                any(
                    reverse[str(citation.document_version_id)] == anchor.source_key
                    and anchor.excerpt in citation.excerpt
                    for citation in citations
                )
                for anchor in target_item.required_evidence
            )
            matches = item.state == target_item.state if item else False
            items.append(
                {
                    "key": target_item.key,
                    "description": target_item.description,
                    "expectedState": target_item.state,
                    "observedIndex": index,
                    "observedState": item.state if item else None,
                    "observedCondition": item.condition if item else None,
                    "mappingReason": mapping.reason if reviewed and mapping else None,
                    "present": item is not None,
                    "stateMatch": matches,
                    "requiredEvidence": len(target_item.required_evidence),
                    "coveredRequiredEvidence": covered,
                    "passed": matches and covered == len(target_item.required_evidence),
                }
            )
        unexpected = sorted(set(range(len(actual))) - set(indexes)) if reviewed else []
        rows.append(
            {
                "key": key,
                "observable": observable,
                "reviewed": reviewed,
                "statusMatch": baseline_rows[key]["statusMatch"],
                "expectedPrerequisites": len(items),
                "observedPrerequisites": len(actual) if observable else None,
                "stateMatches": sum(item["stateMatch"] for item in items),
                "stateMismatches": sum(
                    item["present"] and not item["stateMatch"] for item in items
                ),
                "missingPrerequisites": sum(not item["present"] for item in items)
                if reviewed
                else None,
                "unexpectedIndexes": unexpected,
                "items": items,
                "passed": observable
                and reviewed
                and not unexpected
                and all(item["passed"] for item in items),
            }
        )
    return {
        "schemaVersion": "presales-prerequisite-score-v1",
        "scope": "original_accepted_prerequisites_with_explicit_review_mapping",
        "datasetSha256": digest,
        "originalRunSha256": run_hash,
        "expectationsSha256": expected_hash,
        "reviewSha256": hashlib.sha256(review_raw).hexdigest(),
        "reviewer": review.reviewer,
        "reviewType": review.review_type,
        "referenceReviewStatus": expected.review_status,
        "expectedRows": len(rows),
        "observableRows": sum(row["observable"] for row in rows),
        "reviewedRows": sum(row["reviewed"] for row in rows),
        "passedRows": sum(row["passed"] for row in rows),
        "realProviderRequests": baseline["realProviderRequests"],
        "newProviderRequests": 0,
        "prerequisiteCheckPassed": all(row["passed"] for row in rows),
        "semanticReviewRequired": True,
        "independentDomainReview": False,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "gold", "run", "expectations", "review", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = score_prerequisites(args.input, args.gold, args.run, args.expectations, args.review)
    write_json(args.output, result, exclusive=True)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    raise SystemExit(0 if result["prerequisiteCheckPassed"] else 1)


if __name__ == "__main__":
    main()
