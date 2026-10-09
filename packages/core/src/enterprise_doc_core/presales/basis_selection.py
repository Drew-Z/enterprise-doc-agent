"""Separate prerequisite definitions, missing records and directional support."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from enterprise_doc_core.presales.citation_selection import CitationReference
from enterprise_doc_core.presales.errors import OutputContractError, OutputDiagnostic
from enterprise_doc_core.presales.evidence_selection import EvidenceQuote
from enterprise_doc_core.presales.proposition_selection import (
    PropositionDraft,
    resolve_proposition_selection,
)
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    Status,
    TextItem,
)


class BasisPrerequisite(PresalesModel):
    proposition: str = Field(min_length=1, max_length=995)
    definition: list[EvidenceQuote] = Field(min_length=1, max_length=12)
    unconfirmed: list[EvidenceQuote] = Field(max_length=12)
    positive: list[EvidenceQuote] = Field(max_length=12)
    negative: list[EvidenceQuote] = Field(max_length=12)
    uncertainty: Literal["none", "missing", "conflict"]

    @model_validator(mode="after")
    def distinct_gap(self) -> Self:
        if self.unconfirmed and self.uncertainty != "missing":
            raise ValueError("unconfirmed information requires missing certainty")
        return self


class BasisDraft(PresalesModel):
    prerequisites: list[BasisPrerequisite] = Field(max_length=12)
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(default_factory=list, max_length=12)
    citations: list[CitationReference] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unknown_question(self) -> Self:
        if (
            any(item.uncertainty != "none" for item in self.prerequisites)
            and not self.missing_information
        ):
            raise ValueError("unknown prerequisite requires a confirmation question")
        return self


def resolve_basis(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = BasisDraft.model_validate_json(content)
    projected = []
    for item in selected.prerequisites:
        references: list[str] = []
        for quote in [*item.definition, *item.unconfirmed]:
            source = catalog.get(quote.citation_id)
            if source is None or quote.text not in source.excerpt:
                raise OutputContractError(OutputDiagnostic.BASIS_QUOTE)
            if quote.citation_id not in references:
                references.append(quote.citation_id)
        projected.append(
            {
                "proposition": item.proposition,
                "uncertainty": item.uncertainty,
                "positive": [q.model_dump(by_alias=True) for q in item.positive],
                "negative": [q.model_dump(by_alias=True) for q in item.negative],
                "citations": [{"citationId": ref} for ref in references],
            }
        )
    legacy = PropositionDraft.model_validate(
        {
            "prerequisites": projected,
            "status": selected.status,
            "answer": selected.answer,
            "missingInformation": selected.missing_information,
            "citations": [c.model_dump(by_alias=True) for c in selected.citations],
        }
    )
    # Literal containment verifies source identity, not the model's semantic judgment.
    # Historical parsers retain their contracts; public drafts retain their projection.
    return resolve_proposition_selection(legacy.model_dump_json(by_alias=True), catalog)
