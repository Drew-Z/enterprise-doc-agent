from __future__ import annotations

from uuid import uuid4

import pytest
import scripts.evaluate_postgres_hybrid_candidate as evaluator

from enterprise_doc_core.documents.retrieval import RetrievalCandidate, RetrievalDecision
from enterprise_doc_core.evaluation.rag_quality import (
    RagExpectedOutcome,
    RagQualityCase,
    RagQualityCategory,
)


@pytest.mark.asyncio
async def test_precomputed_query_provider_reuses_only_known_vectors() -> None:
    provider = evaluator.PrecomputedQueryEmbeddingProvider({"known": (1.0, 2.0)})

    assert await provider.embed(("known",)) == ((1.0, 2.0),)
    with pytest.raises(ValueError, match="unexpected query"):
        await provider.embed(("unknown",))


def test_case_results_keep_generic_scores_and_aggregate_gate_metrics() -> None:
    tenant_id = uuid4()
    version_id = uuid4()
    generation_id = uuid4()
    answer_chunk_id = uuid4()
    refusal_chunk_id = uuid4()
    answer_local = evaluator.LocalChunk(
        document_key="policy",
        chunk_index=0,
        text="retention policy",
        chunk_id="answer-local",
        anchor_ids=("policy.retention",),
    )
    refusal_local = evaluator.LocalChunk(
        document_key="policy",
        chunk_index=1,
        text="unrelated policy",
        chunk_id="refusal-local",
        anchor_ids=(),
    )
    seeded = evaluator.SeededDocument(
        version_id=version_id,
        chunk_ids={answer_chunk_id: answer_local, refusal_chunk_id: refusal_local},
    )
    answer_case = RagQualityCase(
        case_id="answer",
        category=RagQualityCategory.FACT,
        document_key="policy",
        query="What is the retention policy?",
        expected_outcome=RagExpectedOutcome.ANSWER,
        expected_anchor_ids=("policy.retention",),
    )
    refusal_case = RagQualityCase(
        case_id="refusal",
        category=RagQualityCategory.REFUSAL,
        document_key="policy",
        query="What is the stock price?",
        expected_outcome=RagExpectedOutcome.REFUSAL,
        accepted_refusal_codes=("low_relevance",),
    )
    answer_decision = RetrievalDecision(
        accepted=True,
        candidates=(
            RetrievalCandidate(
                chunk_id=answer_chunk_id,
                tenant_id=tenant_id,
                document_version_id=version_id,
                generation_id=generation_id,
                text=answer_local.text,
                score=0.032,
            ),
        ),
    )
    refusal_decision = RetrievalDecision(
        accepted=False,
        candidates=(
            RetrievalCandidate(
                chunk_id=refusal_chunk_id,
                tenant_id=tenant_id,
                document_version_id=version_id,
                generation_id=generation_id,
                text=refusal_local.text,
                score=0.2,
            ),
        ),
    )

    answer = evaluator._case_result(answer_case, answer_decision, seeded, ks=(1, 3))
    refusal = evaluator._case_result(refusal_case, refusal_decision, seeded, ks=(1, 3))
    metrics = evaluator._aggregate((answer, refusal), ks=(1, 3))

    assert answer["top_chunks"] == [
        {"rank": 1, "chunk_id": "answer-local", "anchor_ids": ["policy.retention"], "score": 0.032}
    ]
    assert refusal["metrics"] == {"accepted": False, "top1_score": 0.2}
    assert metrics["answer_anchor_recall_at_1"] == 1.0
    assert metrics["answer_mrr"] == 1.0
    assert metrics["refusal_acceptance_rate"] == 0.0
    assert metrics["refusal_top1_score_max"] == 0.2
