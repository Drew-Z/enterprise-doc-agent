from __future__ import annotations

from uuid import UUID

import pytest

from enterprise_doc_core.documents.retrieval import (
    Citation,
    RefusalReason,
    RetrievalCandidate,
    authorize_candidates,
    decide_retrieval,
    reciprocal_rank_fusion,
    validate_citations,
)
from enterprise_doc_core.documents.retrieval_service import format_embedding_query

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT = UUID("00000000-0000-0000-0000-000000000002")
VERSION = UUID("00000000-0000-0000-0000-000000000010")
OTHER_VERSION = UUID("00000000-0000-0000-0000-000000000011")
GENERATION = UUID("00000000-0000-0000-0000-000000000020")


def candidate(
    number: int, *, tenant_id: UUID = TENANT, version: UUID = VERSION, score: float = 0.0
) -> RetrievalCandidate:
    chunk_id = UUID(f"00000000-0000-0000-0000-{number:012d}")
    return RetrievalCandidate(
        chunk_id=chunk_id,
        tenant_id=tenant_id,
        document_version_id=version,
        generation_id=GENERATION,
        text=f"evidence {number}",
        score=score,
    )


def test_rrf_deduplicates_and_is_deterministic() -> None:
    output = reciprocal_rank_fusion((candidate(1), candidate(2)), (candidate(2), candidate(3)))

    assert [item.chunk_id for item in output] == [
        candidate(2).chunk_id,
        candidate(1).chunk_id,
        candidate(3).chunk_id,
    ]
    assert output[0].score > output[1].score


def test_qwen_query_instruction_is_not_applied_to_unconfigured_routes() -> None:
    query = "What are the payment terms?"

    assert format_embedding_query(query, None) == query
    assert format_embedding_query(query, "  Retrieve relevant contract passages  ") == (
        "Instruct: Retrieve relevant contract passages\nQuery:What are the payment terms?"
    )


def test_authorization_filters_tenant_and_version_before_model_context() -> None:
    output = authorize_candidates(
        (candidate(1), candidate(2, tenant_id=OTHER_TENANT), candidate(3, version=OTHER_VERSION)),
        tenant_id=TENANT,
        document_version_id=VERSION,
    )

    assert [item.chunk_id for item in output] == [candidate(1).chunk_id]


def test_refusal_is_returned_for_empty_or_low_evidence() -> None:
    assert decide_retrieval(()).refusal_reason == RefusalReason.EMPTY_EVIDENCE
    assert (
        decide_retrieval((candidate(1, score=0.2),), min_candidates=2).refusal_reason
        == RefusalReason.INSUFFICIENT_EVIDENCE
    )
    assert (
        decide_retrieval((candidate(1, score=0.1),), min_score=0.2).refusal_reason
        == RefusalReason.LOW_RELEVANCE
    )
    assert decide_retrieval((candidate(1, score=0.2),), min_score=0.2).accepted is True

    with pytest.raises(ValueError, match="min_score"):
        decide_retrieval((candidate(1),), min_score=-0.1)
    with pytest.raises(ValueError, match="min_candidates"):
        decide_retrieval((candidate(1),), min_candidates=0)


def test_citation_gate_requires_authorized_candidate_and_excerpt() -> None:
    valid = Citation(candidate(1).chunk_id, VERSION, "evidence 1")

    resolved = validate_citations(
        (valid,), (candidate(1),), tenant_id=TENANT, document_version_id=VERSION
    )
    assert resolved[0].excerpt == "evidence 1"

    with pytest.raises(ValueError, match=RefusalReason.CITATION_WRONG_VERSION.value):
        validate_citations(
            (Citation(candidate(1).chunk_id, OTHER_VERSION, "evidence 1"),),
            (candidate(1),),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )

    with pytest.raises(ValueError, match=RefusalReason.CITATION_NOT_AUTHORIZED.value):
        validate_citations(
            (valid,),
            (candidate(1, tenant_id=OTHER_TENANT),),
            tenant_id=TENANT,
            document_version_id=VERSION,
        )

    with pytest.raises(ValueError, match=RefusalReason.CITATION_NOT_IN_CANDIDATES.value):
        validate_citations(
            (valid,),
            (candidate(1),),
            tenant_id=TENANT,
            document_version_id=VERSION,
            max_excerpt_chars=5,
        )
