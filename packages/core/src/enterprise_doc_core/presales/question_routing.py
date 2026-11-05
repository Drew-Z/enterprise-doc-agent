"""Isolated typed question-role candidate; deliberately not a runtime gateway option."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from enterprise_doc_core.presales.citation_selection import CitationReference, SelectionDraft
from enterprise_doc_core.presales.question_assessment import (
    CONTEXT_SYSTEM_PROMPT,
    QuestionAssessmentDraft,
    resolve_question_assessment,
)
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PresalesModel,
    RequirementInput,
    TextItem,
)

PROTOCOL_VERSION = "presales.question-routing-candidate.v1"


class FactEvidence(CitationReference):
    role: Literal["policy", "state"]


class KnownFinding(PresalesModel):
    kind: Literal["known"]
    requested_evidence: Literal["description", "execution"]
    answer: TextItem
    evidence: list[FactEvidence] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def execution_needs_state(self) -> KnownFinding:
        if self.requested_evidence == "execution" and not any(
            ref.role == "state" for ref in self.evidence
        ):
            raise ValueError("Known execution requires declared state evidence")
        return self


class UnknownFinding(PresalesModel):
    kind: Literal["unknown"]
    requested_evidence: Literal["description", "execution"]
    answer: TextItem
    context: list[FactEvidence] = Field(max_length=12)
    question: TextItem


class ConflictingFinding(PresalesModel):
    kind: Literal["conflict"]
    requested_evidence: Literal["description", "execution"]
    answer: TextItem
    evidence: list[FactEvidence] = Field(min_length=2, max_length=12)
    question: TextItem


class FactualResult(PresalesModel):
    mode: Literal["facts"]
    requirement_part_id: str = Field(min_length=1, max_length=64)
    findings: list[KnownFinding | UnknownFinding | ConflictingFinding] = Field(
        min_length=1, max_length=12
    )
    status: Literal["supported", "insufficient_evidence", "conflicting_evidence"]
    conclusion: TextItem

    @model_validator(mode="after")
    def status_matches_findings(self) -> FactualResult:
        expected = (
            "conflicting_evidence"
            if any(isinstance(fact, ConflictingFinding) for fact in self.findings)
            else "insufficient_evidence"
            if any(isinstance(fact, UnknownFinding) for fact in self.findings)
            else "supported"
        )
        if self.status != expected:
            raise ValueError("Status must preserve declared unknown or conflicting findings")
        return self


class ConditionalResult(PresalesModel):
    mode: Literal["conditions"]
    draft: QuestionAssessmentDraft


class RoutedQuestionDraft(PresalesModel):
    result: FactualResult | ConditionalResult


def routed_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "presales_routed_question",
            "strict": True,
            "schema": RoutedQuestionDraft.model_json_schema(),
        },
    }


def routed_system_message() -> str:
    # Classification is model-selected inside the same bounded response, never a
    # keyword switch or an additional classifier/judge request.
    prompt = (
        CONTEXT_SYSTEM_PROMPT
        + """
本候选改用result封装以下两种互斥任务结构。先根据实际询问选择任务\uff0c不根据题号或关键词选择。
facts\uff1a问题要求说明政策、能力、范围、数值或实际执行情况\uff0c不要求判断业务履约条件时使用。
facts没有rules、assessments或业务前提\uff1b按实际子问题填写findings\uff0c不把背景和选项逐项改写成问题。
每个finding直接回答一个实际询问\uff1b政策、规则、计划的描述选description\uff1b询问已落实、覆盖、
执行或运行状态选execution。known必须引用同范围依据。evidence.role=policy表示规定、程序、
计划或要求\uff1bstate表示直接记载当前事实或执行情况。政策要求和管理程序不能自动证明实际执行\uff0c
执行情况未知时用unknown\uff0c保留有用的政策context\uff0c并用question询问具体未决事实。
同范围记录矛盾且没有明确优先级时用conflict\uff0c引用双方并提出具体澄清问题。不要为减少缺口而
把policy改标为state。上述角色只是你对原文的判断\uff0c须核对主体、时间、动作、范围和限定。
facts的status在有conflict时为conflicting_evidence\uff0c否则有unknown时为insufficient_evidence\uff0c
否则为supported。描述性问题的否定答案也可以有充分证据\uff1b不把“否”自动解释为业务不满足。
conditions\uff1a实际询问资格、启用前提、合同义务或履约是否满足时使用\uff1bdraft沿用上述完整评估结构。
混合题保留事实回答\uff0c同时只对真正必要的业务条件生成rules和assessments。客户要求本身不证明履约。
两种结构都保留完整requirement的唯一requirementPartId\uff0c引用只选本次提供的编号。不得改写或修补引用。
"""
    )
    return prompt + "\n" + json.dumps(RoutedQuestionDraft.model_json_schema(), ensure_ascii=False)


def resolve_routed_question(
    content: str, requirement: RequirementInput, catalog: dict[str, CitationInput]
) -> ModelDraft:
    result = RoutedQuestionDraft.model_validate_json(content).result
    if isinstance(result, ConditionalResult):
        value = result.draft.model_dump(by_alias=True)
    else:
        citations: list[str] = []
        gaps = []
        answers = []
        for fact in result.findings:
            # Validate each original prose field before joining; Chinese text in a
            # different finding must not mask an English-only answer or question.
            SelectionDraft(prerequisites=[], status="supported", answer=fact.answer)
            answers.append(fact.answer)
            refs = fact.context if isinstance(fact, UnknownFinding) else fact.evidence
            if isinstance(fact, ConflictingFinding):
                # Each claimed conflict must identify both source versions itself;
                # an unrelated citation in another finding is not its second side.
                if any(ref.citation_id not in catalog for ref in refs):
                    raise ValueError("Conflict contains an unknown citation")
                if len({catalog[ref.citation_id].document_version_id for ref in refs}) < 2:
                    raise ValueError("Conflict requires evidence from distinct source versions")
            citations.extend(ref.citation_id for ref in refs)
            if isinstance(fact, (UnknownFinding, ConflictingFinding)):
                SelectionDraft(prerequisites=[], status="supported", answer=fact.question)
                gaps.append(fact.question)
        value = {
            "rules": [],
            "assessments": [],
            "responses": [
                {
                    "requirementPartId": result.requirement_part_id,
                    "answer": "\n".join(answers),
                    "citations": [{"citationId": key} for key in dict.fromkeys(citations)],
                    "missingInformation": list(dict.fromkeys(gaps)),
                }
            ],
            "status": result.status,
            "conclusion": result.conclusion,
        }
    return resolve_question_assessment(
        json.dumps(value, ensure_ascii=False),
        requirement,
        catalog,
        expanded_missing_information=True,
        whole_requirement=True,
    )
