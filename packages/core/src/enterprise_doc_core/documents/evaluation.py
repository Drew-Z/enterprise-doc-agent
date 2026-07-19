from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    case_id: str
    relevant_chunk_ids: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    expected_refusal: bool
    predicted_refusal: bool
    golden_citation_ids: tuple[str, ...] = ()
    predicted_citation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalEvalReport:
    case_count: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    citation_precision: float
    refusal_precision: float
    refusal_recall: float


def _recall_at_k(case: RetrievalEvalCase, k: int) -> float:
    relevant = set(case.relevant_chunk_ids)
    if not relevant:
        return 1.0
    return len(relevant.intersection(case.retrieved_chunk_ids[:k])) / len(relevant)


def _reciprocal_rank(case: RetrievalEvalCase) -> float:
    relevant = set(case.relevant_chunk_ids)
    for rank, chunk_id in enumerate(case.retrieved_chunk_ids, start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(case: RetrievalEvalCase, k: int) -> float:
    relevant = set(case.relevant_chunk_ids)
    if not relevant:
        return 1.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(case.retrieved_chunk_ids[:k], start=1)
        if chunk_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def evaluate_retrieval_cases(
    cases: tuple[RetrievalEvalCase, ...], *, k: int = 5
) -> RetrievalEvalReport:
    if not cases:
        raise ValueError("at least one evaluation case is required")
    if k <= 0:
        raise ValueError("k must be positive")
    retrieval_cases = tuple(case for case in cases if not case.expected_refusal)
    if not retrieval_cases:
        recall = mrr = ndcg = 0.0
    else:
        recall = sum(_recall_at_k(case, k) for case in retrieval_cases) / len(retrieval_cases)
        mrr = sum(_reciprocal_rank(case) for case in retrieval_cases) / len(retrieval_cases)
        ndcg = sum(_ndcg_at_k(case, k) for case in retrieval_cases) / len(retrieval_cases)

    predicted_citations = sum(len(case.predicted_citation_ids) for case in cases)
    correct_citations = sum(
        len(set(case.predicted_citation_ids).intersection(case.golden_citation_ids))
        for case in cases
    )
    expected_citations = sum(len(case.golden_citation_ids) for case in cases)
    citation_precision = (
        correct_citations / predicted_citations
        if predicted_citations
        else (0.0 if expected_citations else 1.0)
    )

    true_positive_refusals = sum(case.expected_refusal and case.predicted_refusal for case in cases)
    predicted_refusals = sum(case.predicted_refusal for case in cases)
    expected_refusals = sum(case.expected_refusal for case in cases)
    refusal_precision = true_positive_refusals / predicted_refusals if predicted_refusals else 1.0
    refusal_recall = true_positive_refusals / expected_refusals if expected_refusals else 1.0
    return RetrievalEvalReport(
        case_count=len(cases),
        recall_at_k=recall,
        mrr=mrr,
        ndcg_at_k=ndcg,
        citation_precision=citation_precision,
        refusal_precision=refusal_precision,
        refusal_recall=refusal_recall,
    )
