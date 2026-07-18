from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RefusalReason(StrEnum):
    EMPTY_EVIDENCE = "empty_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LOW_RELEVANCE = "low_relevance"
    CITATION_NOT_AUTHORIZED = "citation_not_authorized"
    CITATION_WRONG_VERSION = "citation_wrong_version"
    CITATION_NOT_IN_CANDIDATES = "citation_not_in_candidates"


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    tenant_id: UUID
    document_version_id: UUID
    generation_id: UUID
    text: str
    page_number: int | None = None
    heading: str | None = None
    start_offset: int = 0
    end_offset: int = 0
    source_filename: str | None = None
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class Citation:
    chunk_id: UUID
    document_version_id: UUID
    excerpt: str


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    chunk_id: UUID
    document_version_id: UUID
    source_filename: str | None
    page_number: int | None
    heading: str | None
    start_offset: int
    end_offset: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class RetrievalDecision:
    accepted: bool
    candidates: tuple[RetrievalCandidate, ...]
    refusal_reason: RefusalReason | None = None


def reciprocal_rank_fusion(
    keyword_candidates: tuple[RetrievalCandidate, ...],
    vector_candidates: tuple[RetrievalCandidate, ...],
    *,
    rrf_k: int = 60,
    top_k: int = 10,
) -> tuple[RetrievalCandidate, ...]:
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    merged: dict[UUID, RetrievalCandidate] = {}
    scores: dict[UUID, float] = {}
    for candidates in (keyword_candidates, vector_candidates):
        for rank, candidate in enumerate(candidates, start=1):
            merged.setdefault(candidate.chunk_id, candidate)
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(
        (candidate for candidate in merged.values()),
        key=lambda candidate: (-scores[candidate.chunk_id], str(candidate.chunk_id)),
    )
    return tuple(
        RetrievalCandidate(
            chunk_id=candidate.chunk_id,
            tenant_id=candidate.tenant_id,
            document_version_id=candidate.document_version_id,
            generation_id=candidate.generation_id,
            text=candidate.text,
            page_number=candidate.page_number,
            heading=candidate.heading,
            start_offset=candidate.start_offset,
            end_offset=candidate.end_offset,
            source_filename=candidate.source_filename,
            score=scores[candidate.chunk_id],
        )
        for candidate in ranked[:top_k]
    )


def authorize_candidates(
    candidates: tuple[RetrievalCandidate, ...], *, tenant_id: UUID, document_version_id: UUID
) -> tuple[RetrievalCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if candidate.tenant_id == tenant_id and candidate.document_version_id == document_version_id
    )


def decide_retrieval(
    candidates: tuple[RetrievalCandidate, ...],
    *,
    min_score: float = 0.0,
    min_candidates: int = 1,
) -> RetrievalDecision:
    if min_score < 0:
        raise ValueError("min_score must be non-negative")
    if min_candidates <= 0:
        raise ValueError("min_candidates must be positive")
    if not candidates:
        return RetrievalDecision(False, (), RefusalReason.EMPTY_EVIDENCE)
    if len(candidates) < min_candidates:
        return RetrievalDecision(False, candidates, RefusalReason.INSUFFICIENT_EVIDENCE)
    if candidates[0].score < min_score:
        return RetrievalDecision(False, candidates, RefusalReason.LOW_RELEVANCE)
    return RetrievalDecision(True, candidates)


def validate_citations(
    citations: tuple[Citation, ...],
    candidates: tuple[RetrievalCandidate, ...],
    *,
    tenant_id: UUID,
    document_version_id: UUID,
    max_excerpt_chars: int = 500,
) -> tuple[ResolvedCitation, ...]:
    if max_excerpt_chars <= 0:
        raise ValueError("max_excerpt_chars must be positive")
    authorized = {
        candidate.chunk_id: candidate
        for candidate in authorize_candidates(
            candidates,
            tenant_id=tenant_id,
            document_version_id=document_version_id,
        )
    }
    candidate_ids = {candidate.chunk_id for candidate in candidates}
    resolved: list[ResolvedCitation] = []
    for citation in citations:
        if citation.document_version_id != document_version_id:
            raise ValueError(RefusalReason.CITATION_WRONG_VERSION.value)
        if citation.chunk_id not in candidate_ids:
            raise ValueError(RefusalReason.CITATION_NOT_IN_CANDIDATES.value)
        candidate = authorized.get(citation.chunk_id)
        if candidate is None:
            raise ValueError(RefusalReason.CITATION_NOT_AUTHORIZED.value)
        excerpt = citation.excerpt.strip()
        if not excerpt or len(excerpt) > max_excerpt_chars or excerpt not in candidate.text:
            raise ValueError(RefusalReason.CITATION_NOT_IN_CANDIDATES.value)
        resolved.append(
            ResolvedCitation(
                chunk_id=candidate.chunk_id,
                document_version_id=candidate.document_version_id,
                source_filename=candidate.source_filename,
                page_number=candidate.page_number,
                heading=candidate.heading,
                start_offset=candidate.start_offset,
                end_offset=candidate.end_offset,
                excerpt=excerpt,
            )
        )
    return tuple(resolved)
