"""Expose valid evidence combinations to provider decoding, not only local validation."""

from typing import Any, Literal

from pydantic import Field

from enterprise_doc_core.presales.basis_selection import BasisPrerequisite, resolve_basis
from enterprise_doc_core.presales.citation_selection import CitationReference
from enterprise_doc_core.presales.evidence_selection import EvidenceQuote
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    Status,
    TextItem,
)


class PositiveSupport(BasisPrerequisite):
    uncertainty: Literal["none"]
    positive: list[EvidenceQuote] = Field(min_length=1, max_length=12)
    negative: list[EvidenceQuote] = Field(max_length=0)
    unconfirmed: list[EvidenceQuote] = Field(max_length=0)


class NegativeSupport(BasisPrerequisite):
    uncertainty: Literal["none"]
    positive: list[EvidenceQuote] = Field(max_length=0)
    negative: list[EvidenceQuote] = Field(min_length=1, max_length=12)
    unconfirmed: list[EvidenceQuote] = Field(max_length=0)


class MissingSupport(BasisPrerequisite):
    uncertainty: Literal["missing"]
    positive: list[EvidenceQuote] = Field(max_length=0)
    negative: list[EvidenceQuote] = Field(max_length=0)


class ConflictingSupport(BasisPrerequisite):
    uncertainty: Literal["conflict"]
    positive: list[EvidenceQuote] = Field(min_length=1, max_length=12)
    negative: list[EvidenceQuote] = Field(min_length=1, max_length=12)
    unconfirmed: list[EvidenceQuote] = Field(max_length=0)


class ConstrainedBasisDraft(PresalesModel):
    # Keep explicit alternatives: absence of records and genuine conflict must
    # remain representable. This does not prove atomicity or quote entailment.
    prerequisites: list[PositiveSupport | NegativeSupport | MissingSupport | ConflictingSupport] = (
        Field(max_length=12)
    )
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(max_length=12)
    citations: list[CitationReference] = Field(max_length=12)


def constrained_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "presales_response",
            "strict": True,
            "schema": ConstrainedBasisDraft.model_json_schema(),
        },
    }


def resolve_constrained_basis(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    ConstrainedBasisDraft.model_validate_json(content)
    return resolve_basis(content, catalog)
