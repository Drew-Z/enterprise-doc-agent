from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_doc_core.evaluation.rag_quality import (
    ObservedCitation,
    RagQualityObservation,
    aggregate_rag_quality_scores,
    load_rag_quality_dataset,
    score_rag_quality_case,
)


def _dataset_payload(*, document_path: str = "policy.txt") -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": "rag-quality-test-v1",
        "corpus_root": "corpus",
        "expected_category_counts": {"fact": 1, "refusal": 1},
        "documents": [
            {
                "document_key": "leave-policy",
                "path": document_path,
                "media_type": "text/plain",
                "anchors": [
                    {
                        "anchor_id": "leave.carryover",
                        "section": "Vacation Carryover",
                        "page": None,
                        "quote": "carry over up to five unused vacation days",
                    }
                ],
            }
        ],
        "cases": [
            {
                "case_id": "fact-carryover",
                "category": "fact",
                "document_key": "leave-policy",
                "query": "How many vacation days may an employee carry over?",
                "expected_outcome": "answer",
                "facts": [
                    {
                        "fact_id": "carryover-days",
                        "accepted_answers": ["five unused vacation days", "5 vacation days"],
                        "forbidden_answers": ["ten vacation days", "10 vacation days"],
                        "anchor_ids": ["leave.carryover"],
                    }
                ],
                "expected_anchor_ids": ["leave.carryover"],
                "accepted_refusal_codes": [],
                "trial": True,
            },
            {
                "case_id": "refuse-stock-price",
                "category": "refusal",
                "document_key": "leave-policy",
                "query": "What was the closing stock price yesterday?",
                "expected_outcome": "refusal",
                "facts": [],
                "expected_anchor_ids": [],
                "accepted_refusal_codes": ["insufficient_evidence"],
                "trial": True,
            },
        ],
        "targets": {
            "fact_recall": 0.9,
            "closed_label_fact_precision": 0.95,
            "grounded_fact_rate": 0.9,
            "citation_precision": 0.95,
            "citation_recall": 0.9,
            "refusal_precision": 1.0,
            "refusal_recall": 1.0,
            "refusal_reason_accuracy": 1.0,
        },
        "limitations": ["Synthetic test fixture."],
    }


def _write_dataset(tmp_path: Path, payload: dict[str, object]) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "policy.txt").write_text(
        "Vacation Carryover\nEmployees may carry over up to five unused vacation days.\n",
        encoding="utf-8",
    )
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_rag_quality_dataset_validates_sources_and_hashes(tmp_path: Path) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path, _dataset_payload()))

    assert loaded.dataset.version == "rag-quality-test-v1"
    assert len(loaded.dataset.cases) == 2
    assert loaded.documents["leave-policy"].startswith(b"Vacation Carryover")
    assert len(loaded.dataset_sha256) == 64
    assert len(loaded.corpus_sha256) == 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["documents"][0].update({"path": "../outside.txt"}),
            "remain inside corpus_root",
        ),
        (
            lambda payload: payload["documents"][0]["anchors"][0].update(
                {"quote": "a sentence that is absent"}
            ),
            "quote was not found",
        ),
        (
            lambda payload: payload["cases"][0].update({"expected_anchor_ids": ["missing.anchor"]}),
            "unknown anchor",
        ),
    ],
)
def test_load_rag_quality_dataset_rejects_invalid_golden_sources(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    payload = _dataset_payload()
    assert callable(mutate)
    mutate(payload)
    path = _write_dataset(tmp_path, payload)

    with pytest.raises(ValueError, match=message):
        load_rag_quality_dataset(path)


def test_score_uses_stable_anchor_and_does_not_reward_missing_citations(
    tmp_path: Path,
) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path, _dataset_payload()))
    case = loaded.dataset.cases_by_id["fact-carryover"]
    cited = RagQualityObservation(
        terminal_status="succeeded",
        answer_text="The policy permits five unused vacation days to be carried over.",
        citations=(
            ObservedCitation(
                runtime_chunk_id="runtime-chunk-a",
                document_key="leave-policy",
                page=None,
                heading="Vacation Carryover",
                excerpt="Employees may carry over up to five unused vacation days.",
            ),
        ),
        error_code=None,
        duration_ms=120.0,
    )
    same_evidence_new_runtime_id = cited.model_copy(
        update={
            "citations": (
                cited.citations[0].model_copy(update={"runtime_chunk_id": "runtime-chunk-b"}),
            )
        }
    )

    first = score_rag_quality_case(loaded.dataset, case, cited)
    second = score_rag_quality_case(loaded.dataset, case, same_evidence_new_runtime_id)
    missing = score_rag_quality_case(
        loaded.dataset,
        case,
        cited.model_copy(update={"citations": ()}),
    )

    assert first.matched_anchor_ids == ("leave.carryover",)
    assert first.fact_recall == 1.0
    assert first.closed_label_fact_precision == 1.0
    assert first.grounded_fact_rate == 1.0
    assert first.citation_precision == 1.0
    assert first.citation_recall == 1.0
    assert second.matched_anchor_ids == first.matched_anchor_ids
    assert missing.citation_precision == 0.0
    assert missing.citation_recall == 0.0
    assert missing.grounded_fact_rate == 0.0
    assert not missing.passed


def test_aggregate_scores_refusal_and_reason_accuracy(tmp_path: Path) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path, _dataset_payload()))
    fact_case = loaded.dataset.cases_by_id["fact-carryover"]
    refusal_case = loaded.dataset.cases_by_id["refuse-stock-price"]
    fact_score = score_rag_quality_case(
        loaded.dataset,
        fact_case,
        RagQualityObservation(
            terminal_status="succeeded",
            answer_text="Employees may carry over 5 vacation days.",
            citations=(
                ObservedCitation(
                    runtime_chunk_id="chunk",
                    document_key="leave-policy",
                    page=None,
                    heading=None,
                    excerpt="carry over up to five unused vacation days",
                ),
            ),
            duration_ms=50,
        ),
    )
    refusal_score = score_rag_quality_case(
        loaded.dataset,
        refusal_case,
        RagQualityObservation(
            terminal_status="refused",
            answer_text=None,
            citations=(),
            error_code="insufficient_evidence",
            duration_ms=20,
        ),
    )

    aggregate = aggregate_rag_quality_scores((fact_score, refusal_score))

    assert refusal_score.predicted_refusal
    assert refusal_score.refusal_reason_correct is True
    assert aggregate.refusal_precision == 1.0
    assert aggregate.refusal_recall == 1.0
    assert aggregate.refusal_reason_accuracy == 1.0
    assert aggregate.passed_case_count == 2


def test_aggregate_marks_uncovered_refusal_metrics_as_unavailable(tmp_path: Path) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path, _dataset_payload()))
    fact_case = loaded.dataset.cases_by_id["fact-carryover"]
    fact_score = score_rag_quality_case(
        loaded.dataset,
        fact_case,
        RagQualityObservation(
            terminal_status="succeeded",
            answer_text="Employees may carry over 5 vacation days.",
            citations=(
                ObservedCitation(
                    runtime_chunk_id="chunk",
                    document_key="leave-policy",
                    page=None,
                    heading=None,
                    excerpt="carry over up to five unused vacation days",
                ),
            ),
            duration_ms=50,
        ),
    )

    aggregate = aggregate_rag_quality_scores((fact_score,))

    assert aggregate.refusal_precision is None
    assert aggregate.refusal_recall is None
    assert aggregate.refusal_reason_accuracy is None


def test_repository_rag_quality_dataset_has_required_distribution() -> None:
    root = Path(__file__).resolve().parents[3]
    loaded = load_rag_quality_dataset(root / "evaluation" / "rag_quality_v1.json")

    assert len(loaded.dataset.documents) == 8
    assert len(loaded.dataset.cases) == 40
    assert sum(case.trial for case in loaded.dataset.cases) == 12
    assert {
        category.value: count for category, count in loaded.dataset.expected_category_counts.items()
    } == {
        "fact": 18,
        "hard_negative": 8,
        "refusal": 6,
        "citation": 4,
        "safety": 4,
    }
