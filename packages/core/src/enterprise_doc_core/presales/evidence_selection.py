"""Private provider contract; saved drafts retain the existing public projection."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from enterprise_doc_core.presales.citation_selection import (
    CitationReference,
    Prerequisite,
    SelectionDraft,
    resolve_selection,
)
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    Status,
    TextItem,
)


class EvidenceQuote(CitationReference):
    text: str = Field(min_length=1, max_length=600)


class EvidencePrerequisite(PresalesModel):
    proposition: TextItem
    uncertainty: Literal["none", "missing", "conflict"]
    positive: list[EvidenceQuote] = Field(max_length=12)
    negative: list[EvidenceQuote] = Field(max_length=12)
    condition: TextItem
    citations: list[CitationReference] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def consistent_support(self) -> Self:
        positive, negative = bool(self.positive), bool(self.negative)
        if self.uncertainty == "none" and positive == negative:
            raise ValueError("determinate evidence requires exactly one direction")
        if self.uncertainty == "missing" and (positive or negative):
            raise ValueError("missing evidence cannot have a supported direction")
        if self.uncertainty == "conflict" and not (positive and negative):
            raise ValueError("conflicting evidence requires both quoted directions")
        return self

    @property
    def state(self) -> Literal["met", "unmet", "unknown"]:
        if self.positive and not self.negative:
            return "met"
        if self.negative and not self.positive:
            return "unmet"
        return "unknown"


class EvidenceDraft(PresalesModel):
    prerequisites: list[EvidencePrerequisite] = Field(max_length=12)
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(default_factory=list, max_length=12)
    citations: list[CitationReference] = Field(default_factory=list, max_length=12)


def resolve_evidence_selection(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = EvidenceDraft.model_validate_json(content)
    prerequisites = []
    for item in selected.prerequisites:
        references = [ref.citation_id for ref in item.citations]
        if len(references) != len(set(references)):
            raise ValueError("duplicate prerequisite context")
        for quote in [*item.positive, *item.negative]:
            source = catalog.get(quote.citation_id)
            if source is None or quote.text not in source.excerpt:
                raise ValueError("unsupported literal quotation")
            if quote.citation_id not in references:
                references.append(quote.citation_id)
        prerequisites.append(
            Prerequisite(
                state=item.state,
                condition=item.condition,
                citations=[CitationReference(citation_id=ref) for ref in references],
            )
        )
    # Reuse all existing consistency, language and public citation checks. Literal
    # containment proves source identity, not semantic entailment of a proposition.
    normalized = SelectionDraft(
        prerequisites=prerequisites,
        status=selected.status,
        answer=selected.answer,
        missing_information=selected.missing_information,
        citations=selected.citations,
    )
    return resolve_selection(normalized.model_dump_json(by_alias=True), catalog)
