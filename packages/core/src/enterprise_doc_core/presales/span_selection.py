"""Select pre-offered literal spans without asking the provider to copy source text."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import Field

from enterprise_doc_core.presales.citation_selection import CitationReference, SelectionInput
from enterprise_doc_core.presales.errors import OutputContractError, OutputDiagnostic
from enterprise_doc_core.presales.evidence_selection import EvidenceQuote
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    Status,
    TextItem,
)
from enterprise_doc_core.presales.support_contract import resolve_constrained_basis


class OfferedSpan(EvidenceQuote):
    span_id: str = Field(min_length=1, max_length=64)


class SpanSelectionInput(SelectionInput):
    spans: list[OfferedSpan]


def _excerpt_spans(citation_id: str, text: str) -> list[OfferedSpan]:
    # The complete context is always selectable. Lexical boundaries do not assert
    # separate business events, carry a shared subject forward, or drop qualifiers.
    parts = [text]
    start = 0
    for boundary in re.finditer(r"[\u3002\uff01\uff1f\uff1b\r\n]+|[.!?;](?=\s|$)", text):
        parts.append(text[start : boundary.end()].strip())
        start = boundary.end()
    parts.append(text[start:].strip())
    return [
        OfferedSpan(span_id=f"{citation_id}_s{index}", citation_id=citation_id, text=part)
        for index, part in enumerate(dict.fromkeys(p for p in parts if p), start=1)
    ]


def offer_spans(selected: SelectionInput) -> SpanSelectionInput:
    return SpanSelectionInput(
        **selected.model_dump(),
        spans=[
            span
            for evidence in selected.evidence
            for span in _excerpt_spans(evidence.citation_id, evidence.text)
        ],
    )


class SpanReference(PresalesModel):
    span_id: str = Field(min_length=1, max_length=64)


class SpanPrerequisite(PresalesModel):
    proposition: str = Field(min_length=1, max_length=995)
    definition: list[SpanReference] = Field(min_length=1, max_length=12)
    positive: list[SpanReference] = Field(max_length=12)
    negative: list[SpanReference] = Field(max_length=12)
    unconfirmed: list[SpanReference] = Field(max_length=12)


class PositiveSpanSupport(SpanPrerequisite):
    uncertainty: Literal["none"]
    positive: list[SpanReference] = Field(min_length=1, max_length=12)
    negative: list[SpanReference] = Field(max_length=0)
    unconfirmed: list[SpanReference] = Field(max_length=0)


class NegativeSpanSupport(SpanPrerequisite):
    uncertainty: Literal["none"]
    positive: list[SpanReference] = Field(max_length=0)
    negative: list[SpanReference] = Field(min_length=1, max_length=12)
    unconfirmed: list[SpanReference] = Field(max_length=0)


class MissingSpanSupport(SpanPrerequisite):
    uncertainty: Literal["missing"]
    positive: list[SpanReference] = Field(max_length=0)
    negative: list[SpanReference] = Field(max_length=0)


class ConflictingSpanSupport(SpanPrerequisite):
    uncertainty: Literal["conflict"]
    positive: list[SpanReference] = Field(min_length=1, max_length=12)
    negative: list[SpanReference] = Field(min_length=1, max_length=12)
    unconfirmed: list[SpanReference] = Field(max_length=0)


class SpanBasisDraft(PresalesModel):
    prerequisites: list[
        PositiveSpanSupport | NegativeSpanSupport | MissingSpanSupport | ConflictingSpanSupport
    ] = Field(max_length=12)
    status: Status
    answer: str = Field(min_length=1, max_length=4000)
    missing_information: list[TextItem] = Field(max_length=12)
    citations: list[CitationReference] = Field(max_length=12)


def span_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "presales_response",
            "strict": True,
            "schema": SpanBasisDraft.model_json_schema(),
        },
    }


def resolve_span_basis(content: str, catalog: dict[str, CitationInput]) -> ModelDraft:
    selected = SpanBasisDraft.model_validate_json(content)
    spans = {
        span.span_id: span
        for reference, citation in catalog.items()
        for span in _excerpt_spans(reference, citation.excerpt)
    }
    value = selected.model_dump(by_alias=True)
    for item in value["prerequisites"]:
        for field in ("definition", "unconfirmed", "positive", "negative"):
            quotes = []
            for reference in item[field]:
                span = spans.get(reference["spanId"])
                if span is None:
                    raise OutputContractError(
                        OutputDiagnostic.BASIS_QUOTE
                        if field in {"definition", "unconfirmed"}
                        else OutputDiagnostic.SUPPORT_QUOTE
                    )
                quotes.append({"citationId": span.citation_id, "text": span.text})
            item[field] = quotes
    # Only materialize explicit selections. Do not change evidence direction,
    # uncertainty, propositions or prose; all existing business guards still run.
    return resolve_constrained_basis(json.dumps(value, ensure_ascii=False), catalog)
