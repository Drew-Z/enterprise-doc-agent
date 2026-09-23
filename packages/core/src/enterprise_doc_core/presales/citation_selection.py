from __future__ import annotations

import re
from uuid import UUID, uuid4

from pydantic import Field, ValidationError

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    GenerationInput,
    ModelDraft,
    PresalesModel,
    ResponseText,
)


class CitationReference(PresalesModel):
    citation_id: str = Field(min_length=1, max_length=64)


class SelectionDraft(ResponseText):
    """Model-facing selection only; never persisted or exposed as the public draft."""

    citations: list[CitationReference] = Field(default_factory=list, max_length=12)


def split_excerpts(text: str) -> list[str]:
    """Keep ordered source substrings; only discard surrounding whitespace."""
    excerpts = []
    start = 0
    while start < len(text):
        end = min(start + 600, len(text))
        if end < len(text):
            boundaries = [
                match.end()
                for match in re.finditer(
                    r"[\u3002\uff01\uff1f\uff1b]|[.!?;](?=\s)|\s+", text[start:end]
                )
                if match.end() >= 300
            ]
            if boundaries:
                end = start + boundaries[-1]
        excerpt = text[start:end].strip()
        if excerpt:
            excerpts.append(excerpt)
        start = end
    return excerpts


def prepare_citations(payload: GenerationInput) -> tuple[GenerationInput, dict[str, CitationInput]]:
    """Bind this call's references to source text supplied by authorized retrieval."""
    prefix = "cite_" + uuid4().hex[:12]
    versions = {source.version_id for source in payload.sources}
    seen: set[tuple[UUID, UUID]] = set()
    catalog: dict[str, CitationInput] = {}
    evidence = []
    if len(payload.evidence) > 12:
        raise PresalesError("presales_input_too_large")
    try:
        for item in payload.evidence:
            if len(item["text"]) > 1800:
                raise PresalesError("presales_input_too_large")
            chunk, version = UUID(item["chunkId"]), UUID(item["documentVersionId"])
            if version not in versions or (chunk, version) in seen:
                raise PresalesError("presales_invalid_evidence")
            seen.add((chunk, version))
            for excerpt in split_excerpts(item["text"]):
                citation = CitationInput(
                    chunk_id=chunk, document_version_id=version, excerpt=excerpt
                )
                reference = f"{prefix}_{len(catalog) + 1}"
                catalog[reference] = citation
                evidence.append({**item, "text": citation.excerpt, "citationId": reference})
    except (KeyError, ValueError, ValidationError) as error:
        raise PresalesError("presales_invalid_evidence") from error
    return payload.model_copy(update={"evidence": evidence}), catalog


def resolve_selection(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = SelectionDraft.model_validate_json(content)
    references = [citation.citation_id for citation in selected.citations]
    if len(set(references)) != len(references) or any(key not in catalog for key in references):
        raise PresalesError("presales_invalid_citation", provider_requests=1)
    return ModelDraft(
        **selected.model_dump(exclude={"citations"}),
        citations=[catalog[key] for key in references],
    )
