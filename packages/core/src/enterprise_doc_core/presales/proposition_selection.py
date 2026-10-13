"""Project assessed business propositions without a second model-written description."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from enterprise_doc_core.presales.citation_selection import CitationReference
from enterprise_doc_core.presales.evidence_selection import (
    EvidenceDraft,
    EvidencePrerequisite,
    EvidenceQuote,
    resolve_evidence_selection,
)
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    Status,
    TextItem,
    prerequisite_conditions,
)


class PropositionPrerequisite(PresalesModel):
    # The immutable description stays neutral when a reviewer changes the state.
    proposition: str = Field(min_length=1, max_length=995)
    uncertainty: Literal["none", "missing", "conflict"]
    positive: list[EvidenceQuote] = Field(max_length=12)
    negative: list[EvidenceQuote] = Field(max_length=12)
    citations: list[CitationReference] = Field(min_length=1, max_length=12)


class PropositionDraft(PresalesModel):
    prerequisites: list[PropositionPrerequisite] = Field(max_length=12)
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(default_factory=list, max_length=12)
    citations: list[CitationReference] = Field(default_factory=list, max_length=12)


def resolve_proposition_selection(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = PropositionDraft.model_validate_json(content)
    evidence = EvidenceDraft(
        prerequisites=[
            EvidencePrerequisite(**item.model_dump(), condition=item.proposition)
            for item in selected.prerequisites
        ],
        status=selected.status,
        answer=selected.answer,
        missing_information=selected.missing_information,
        citations=selected.citations,
    )
    # Validate support, references and the proposition's own language before adding
    # Chinese prefixes: a prefix must not make English-only model prose acceptable.
    draft = resolve_evidence_selection(evidence.model_dump_json(by_alias=True), catalog)
    prerequisites = [
        item.model_copy(update={"condition": "核验事项\uff1a" + item.condition})
        for item in draft.prerequisites or []
    ]
    return ModelDraft.model_validate(
        {
            **draft.model_dump(),
            "prerequisites": [item.model_dump() for item in prerequisites],
            "conditions": prerequisite_conditions(prerequisites),
        }
    )
