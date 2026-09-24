from __future__ import annotations

import re
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, model_validator

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    GenerationInput,
    ModelDraft,
    PrerequisiteAssessment,
    PresalesModel,
    RequirementInput,
    Status,
    TextItem,
    prerequisite_conditions,
)


class CitationReference(PresalesModel):
    citation_id: str = Field(min_length=1, max_length=64)


class SelectionSource(PresalesModel):
    label: str
    filename: str
    applicability: str
    version_number: int
    latest_version_number: int


class SelectionEvidence(CitationReference):
    text: str = Field(min_length=1, max_length=600)
    source: SelectionSource
    heading: str = ""
    page_number: str = ""


class SelectionInput(PresalesModel):
    """Provider view; internal source identities remain in the call-local catalog."""

    requirement: RequirementInput
    evidence: list[SelectionEvidence]


class Prerequisite(PresalesModel):
    condition: TextItem
    state: Literal["met", "unmet", "unknown"]
    citations: list[CitationReference] = Field(min_length=1, max_length=12)


class SelectionDraft(PresalesModel):
    """Model-facing selection only; never persisted or exposed as the public draft."""

    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(default_factory=list, max_length=12)
    prerequisites: list[Prerequisite] = Field(max_length=12)
    citations: list[CitationReference] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def consistent_prerequisites(self) -> Self:
        if self.status == "conflicting_evidence" and not self.missing_information:
            raise ValueError("conflicting evidence requires a clarification question")
        if self.status == "insufficient_evidence" and not self.missing_information:
            raise ValueError("insufficient evidence requires a follow-up question")
        outstanding = {item.condition for item in self.prerequisites if item.state != "met"}
        if self.status == "supported" and outstanding:
            raise ValueError("supported requires all stated prerequisites to be met")
        if self.status == "conditional" and not outstanding:
            raise ValueError("conditional requires an unmet or unknown prerequisite")
        for item in self.prerequisites:
            references = [citation.citation_id for citation in item.citations]
            if len(set(references)) != len(references):
                raise ValueError("prerequisite references must not contain duplicates")
        return self

    @model_validator(mode="after")
    def chinese_business_prose(self) -> Self:
        # Detect wholly non-Chinese prose; do not rewrite English source quotations
        # or pretend this is full language identification / semantic validation.
        for text in [
            self.answer,
            *(item.condition for item in self.prerequisites),
            *self.missing_information,
        ]:
            if not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", text):
                raise ValueError("generated business prose must include Chinese text")
        return self


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


def prepare_citations(payload: GenerationInput) -> tuple[SelectionInput, dict[str, CitationInput]]:
    """Bind this call's references to source text supplied by authorized retrieval."""
    prefix = "cite_" + uuid4().hex[:12]
    sources = {
        source.version_id: SelectionSource(
            label=f"来源 {index + 1}",
            filename=source.filename,
            applicability=source.applicability,
            version_number=source.version_number,
            latest_version_number=source.latest_version_number,
        )
        for index, source in enumerate(payload.sources)
    }
    if len(sources) != len(payload.sources):
        raise PresalesError("presales_invalid_evidence")
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
            if version not in sources or (chunk, version) in seen:
                raise PresalesError("presales_invalid_evidence")
            seen.add((chunk, version))
            for excerpt in split_excerpts(item["text"]):
                citation = CitationInput(
                    chunk_id=chunk, document_version_id=version, excerpt=excerpt
                )
                reference = f"{prefix}_{len(catalog) + 1}"
                catalog[reference] = citation
                evidence.append(
                    SelectionEvidence(
                        citation_id=reference,
                        text=citation.excerpt,
                        source=sources[version],
                        heading=item.get("heading", ""),
                        page_number=item.get("pageNumber", ""),
                    )
                )
    except (KeyError, ValueError, ValidationError) as error:
        raise PresalesError("presales_invalid_evidence") from error
    return SelectionInput(requirement=payload.requirement, evidence=evidence), catalog


def resolve_selection(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = SelectionDraft.model_validate_json(content)
    references = [citation.citation_id for citation in selected.citations]
    prerequisite_references = [
        citation.citation_id for item in selected.prerequisites for citation in item.citations
    ]
    if len(set(references)) != len(references) or any(
        key not in catalog for key in [*references, *prerequisite_references]
    ):
        raise PresalesError("presales_invalid_citation", provider_requests=1)
    # These are already explicit selections, not inferred or repaired quotations.
    # A source shared by the conclusion and its prerequisites is materialized once.
    references = list(dict.fromkeys([*references, *prerequisite_references]))
    indexes = {key: index for index, key in enumerate(references)}
    prerequisites = [
        PrerequisiteAssessment(
            condition=item.condition,
            state=item.state,
            citation_indexes=[indexes[citation.citation_id] for citation in item.citations],
        )
        for item in selected.prerequisites
    ]
    return ModelDraft(
        **selected.model_dump(exclude={"citations", "prerequisites"}),
        conditions=prerequisite_conditions(prerequisites),
        prerequisites=prerequisites,
        citations=[catalog[key] for key in references],
    )
