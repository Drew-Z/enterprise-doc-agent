from __future__ import annotations

from enterprise_doc_core.documents.evaluation import RetrievalEvalCase, evaluate_retrieval_cases


def test_retrieval_evaluation_reports_ranking_citation_and_refusal_metrics() -> None:
    report = evaluate_retrieval_cases(
        (
            RetrievalEvalCase(
                case_id="qa-1",
                relevant_chunk_ids=("a",),
                retrieved_chunk_ids=("a", "b"),
                expected_refusal=False,
                predicted_refusal=False,
                golden_citation_ids=("a",),
                predicted_citation_ids=("a",),
            ),
            RetrievalEvalCase(
                case_id="qa-2",
                relevant_chunk_ids=("c",),
                retrieved_chunk_ids=("x", "c"),
                expected_refusal=False,
                predicted_refusal=False,
                golden_citation_ids=("c",),
                predicted_citation_ids=("x",),
            ),
            RetrievalEvalCase(
                case_id="refuse-1",
                relevant_chunk_ids=(),
                retrieved_chunk_ids=(),
                expected_refusal=True,
                predicted_refusal=True,
            ),
        ),
        k=2,
    )

    assert report.case_count == 3
    assert report.recall_at_k == 1.0
    assert report.mrr == 0.75
    assert 0.8 < report.ndcg_at_k < 1.0
    assert report.citation_precision == 0.5
    assert report.refusal_precision == 1.0
    assert report.refusal_recall == 1.0


def test_retrieval_evaluation_does_not_reward_missing_required_citations() -> None:
    report = evaluate_retrieval_cases(
        (
            RetrievalEvalCase(
                case_id="missing-citation",
                relevant_chunk_ids=("a",),
                retrieved_chunk_ids=("a",),
                expected_refusal=False,
                predicted_refusal=False,
                golden_citation_ids=("a",),
                predicted_citation_ids=(),
            ),
        )
    )

    assert report.citation_precision == 0.0
