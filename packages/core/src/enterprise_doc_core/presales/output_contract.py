"""Required-field provider schema; business validation stays in the basis resolver."""

from typing import Any

from pydantic import Field

from enterprise_doc_core.presales.basis_selection import BasisDraft, resolve_basis
from enterprise_doc_core.presales.citation_selection import CitationReference
from enterprise_doc_core.presales.schemas import CitationInput, ModelDraft, TextItem


class StrictBasisDraft(BasisDraft):
    # Provider strict schemas require every object field. The legacy wire parser
    # keeps its optional arrays for historical reports and JSON-mode callers.
    missing_information: list[TextItem] = Field(max_length=12)
    citations: list[CitationReference] = Field(max_length=12)


def strict_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "presales_response",
            "strict": True,
            "schema": StrictBasisDraft.model_json_schema(),
        },
    }


def resolve_strict_basis(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    StrictBasisDraft.model_validate_json(content)
    return resolve_basis(content, catalog)
