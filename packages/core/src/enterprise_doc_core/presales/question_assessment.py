"""Private question-ID candidate; not imported by any production gateway."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ConfigDict, Field

from enterprise_doc_core.presales.assessment_selection import (
    SYSTEM_PROMPT as ASSESSMENT_SYSTEM_PROMPT,
)
from enterprise_doc_core.presales.assessment_selection import (
    ConflictingAssessment,
    MetAssessment,
    MissingAssessment,
    NecessityRule,
    UnmetAssessment,
    resolve_assessment,
)
from enterprise_doc_core.presales.citation_selection import CitationReference
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    RequirementInput,
    Status,
    TextItem,
)
from enterprise_doc_core.presales.span_selection import SpanSelectionInput

PROTOCOL_VERSION = "presales.question-assessment-candidate.v1"
SYSTEM_PROMPT = ASSESSMENT_SYSTEM_PROMPT.replace(
    "responses按要求原文顺序覆盖全部内容\uff0c每项requirementText是连续逐字片段\uff0c不遗漏、不重复、不改写问题。",
    "responses按requirementParts的顺序\uff0c每项requirementPartId只选择对应原文片段编号\uff0c"
    "每个编号恰好一次\uff0c不遗漏、不重复、不增加。\n"
    "requirementParts只是原文的标点分段\uff0c不是独立的语义问题\uff1b"
    "须结合完整requirement理解限定与上下文\uff0c回答每个片段的全部要点。",
)


class OfferedRequirementPart(PresalesModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=False)

    requirement_part_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


def offer_requirement_parts(requirement: RequirementInput) -> tuple[OfferedRequirementPart, ...]:
    """Partition literal text, not business meaning; keep a bounded, lossless tail."""
    parts: list[str] = []
    start = 0
    for boundary in re.finditer(
        r"[\u3002\uff01\uff1f\uff1b\r\n]+|[.!?;](?=\s|$)", requirement.text
    ):
        if len(parts) == 11:
            break
        if requirement.text[start : boundary.end()].strip():
            parts.append(requirement.text[start : boundary.end()])
            start = boundary.end()
    tail = requirement.text[start:]
    if tail.strip():
        parts.append(tail)
    elif parts:
        parts[-1] += tail
    prefix = hashlib.sha256(requirement.model_dump_json().encode()).hexdigest()[:24]
    return tuple(
        OfferedRequirementPart(requirement_part_id=f"question_{prefix}_{index}", text=text)
        for index, text in enumerate(parts, start=1)
    )


class QuestionAssessmentInput(SpanSelectionInput):
    requirement_parts: tuple[OfferedRequirementPart, ...] = Field(min_length=1, max_length=12)


def offer_question_assessment(selected: SpanSelectionInput) -> QuestionAssessmentInput:
    return QuestionAssessmentInput(
        **selected.model_dump(), requirement_parts=offer_requirement_parts(selected.requirement)
    )


class QuestionAnswerPart(PresalesModel):
    requirement_part_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=2000)
    citations: list[CitationReference] = Field(max_length=12)
    missing_information: list[TextItem] = Field(max_length=12)


class QuestionAssessmentDraft(PresalesModel):
    rules: list[NecessityRule] = Field(max_length=12)
    assessments: list[
        MetAssessment | UnmetAssessment | MissingAssessment | ConflictingAssessment
    ] = Field(max_length=12)
    responses: list[QuestionAnswerPart] = Field(min_length=1, max_length=12)
    status: Status
    conclusion: TextItem


def question_assessment_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "presales_question_assessment",
            "strict": True,
            "schema": QuestionAssessmentDraft.model_json_schema(),
        },
    }


def question_assessment_system_message() -> str:
    return (
        SYSTEM_PROMPT
        + "\n"
        + json.dumps(QuestionAssessmentDraft.model_json_schema(), ensure_ascii=False)
    )


def resolve_question_assessment(
    content: str, requirement: RequirementInput, catalog: dict[str, CitationInput]
) -> ModelDraft:
    """Materialize only offered text; leave all meaning and legacy guards unchanged."""
    selected = QuestionAssessmentDraft.model_validate_json(content)
    offered = offer_requirement_parts(requirement)
    if [part.requirement_part_id for part in selected.responses] != [
        part.requirement_part_id for part in offered
    ]:
        raise ValueError("responses must cover every offered requirement part once in order")
    value = selected.model_dump(by_alias=True)
    for response, part in zip(value["responses"], offered, strict=True):
        del response["requirementPartId"]
        response["requirementText"] = part.text
    return resolve_assessment(json.dumps(value, ensure_ascii=False), requirement, catalog)
