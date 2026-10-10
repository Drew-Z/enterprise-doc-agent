"""Complement the strongest hybrid hit without another query or a larger budget."""

import math
import re

from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService

_CONTEXT_WORDS = frozenset(
    "yes no your our all both such that those these with some may might not be as or "
    "it its only they everyone etc".split()
)


def select_complementary_evidence(
    candidates: tuple[RetrievalCandidate, ...], query: str
) -> tuple[RetrievalCandidate, ...]:
    """Keep the first hit and prefer novel lexical coverage for the second.

    No semantic classification is performed. Equal scores retain the original
    hybrid rank, including queries with no word overlap. Only visible text counts.
    Authorization belongs to the caller before any candidate is examined.
    """
    pool = candidates[:10]
    if len(pool) <= 2:
        return pool
    terms = set((HybridRetrievalService._keyword_fallback_query(query) or "").split(" OR "))
    terms -= _CONTEXT_WORDS | {""}
    coverage = [
        terms.intersection(re.findall(r"[^\W_]+", item.text[:1800].casefold())) for item in pool
    ]
    weights = {
        term: math.log1p(len(pool) / (1 + sum(term in matched for matched in coverage)))
        for term in terms
    }
    second = max(
        range(1, len(pool)),
        key=lambda index: sum(weights[term] for term in sorted(coverage[index] - coverage[0])),
    )
    return pool[0], pool[second]
