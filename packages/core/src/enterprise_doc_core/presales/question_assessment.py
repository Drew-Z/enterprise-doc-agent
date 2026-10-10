"""Stable question-ID contract with separately versioned generation instructions."""

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

# Keep the original candidate/v21 message reproducible for historical observations.
REVISED_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """
输出前完成以下核对\uff0c只输出既有JSON字段\uff0c不输出检查过程\uff1a
1. 要求清单\uff1a从当前问题及相关原文保留每个独立的角色、经验类别、资格、人数、
年限、比较符、单位和适用范围\uff0c包括问题所指条款中的附加要求。一般工作经验不能替代
特定领域或特定职责的经验\uff1b同一人员同时满足的要求必须分别保留\uff0c不得改成任选其一。
原文确有可选路径时保留其“或”关系。不要因只回答主负责人而漏掉同条款的其他岗位。
2. 状态判定\uff1a先找当前范围的实际状态记录\uff0c再决定每项assessment。采购要求、资格门槛、
必要条件只证明规则\uff0c不能独自证明供应商符合或不符合。只有要求而没有供应商证明时\uff0c
用unknown/missing\uff0cobservations可以为空\uff1b询问具体事实或证明\uff0c不能断言尚未完成。
只有明确未完成、未提交或实际数值不达标的状态证据\uff0c才用unmet。
明确已完成的事实用met\uff0c即使附件缺失\uff1b仅当提交附件本身是明文义务时才另行评估提交。
同一事项存在适用的正反状态记录且无明确优先关系时\uff0c必须用unknown/conflict并分别
填写positive、negative\uff0c不能只在答复中说有冲突而在assessment里填missing。
双方范围不同或仅买方要求与供应商能力不符\uff0c不构成证据之间的冲突。
3. 编号核对\uff1aresponses长度必须等于requirementParts长度\uff0c按原顺序逐个复制编号。
一个问题片段即使涉及多个岗位、条件或询问\uff0c也只生成一个response\uff0c在该answer内
回答全部要点。rules与assessments按业务事项拆分\uff0c不能因此重复生成同一问题编号。
一个片段只对应一个response\uff0c与rules数量无关\uff1b不增加、不遗漏、不重排编号。
4. 一致性\uff1a答复、结论、状态及下一步必须一致。未知事项的下一步是确认实际情况\uff0c
不要预设它未完成或要求补登记一个未经证明的通过结果。未满足事项才要求完成动作。
为弄清测试方法、资源、测量条件而提出的问题放在missingInformation或答复中\uff1b
只有原文明确规定时才列为业务前提。用简短完整的答复保留全部实质要求\uff0c避免重复展开。
"""
)

CLAUSE_COVERAGE_SYSTEM_PROMPT = (
    REVISED_SYSTEM_PROMPT
    + """
5. 条款覆盖\uff1a当问题询问某类服务、责任或条件的完整要求时\uff0c先按资料中的编号逐条核对
该主题的相关条款及其下级条款\uff0c再组织答复。不能只保留含有时限、数字或显著关键词的
主条款而漏掉同主题的其他独立义务。相关条款中的实施阶段、适用对象、合理要求、质量
标准、补救方式、费用承担及例外都要在答复中明确保留\uff1b不必逐字抄录\uff0c也不带入无关主题。
即使多个义务共用一条引用或合写在一个answer中\uff0c也不能省略其责任主体、触发条件或范围。
对没有实际履行资料的义务分别保留未知\uff0c不用一条笼统的“需要承诺”替代要求本身。
输出前对照相关编号检查有无整条遗漏\uff1b在既有字段内简洁表达\uff0c不增加问题编号或调用。
"""
)

NUMERIC_BOUNDARY_SYSTEM_PROMPT = (
    CLAUSE_COVERAGE_SYSTEM_PROMPT
    + """
6. 数值边界\uff1a答复和下一步都保留原文的比较词。没有另行定义时\uff0c“以上”“以下”包含本数\uff0c
“不少于”“不多于”也包含本数\uff1b“超过”“不足”不包含本数。不得把“以上”解释或改写为
“超过”\uff0c也不得把严格大于改为大于等于。分别保留每项的数值、单位、经验类别和适用主体\uff1b
不要为了说明原文而另加更严格的门槛。语境不能确定时保留原词并要求澄清\uff0c不自行定界。
"""
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


def question_assessment_system_message(
    *, revised: bool = False, clause_coverage: bool = False, numeric_boundaries: bool = False
) -> str:
    prompt = (
        CLAUSE_COVERAGE_SYSTEM_PROMPT
        if clause_coverage
        else (REVISED_SYSTEM_PROMPT if revised else SYSTEM_PROMPT)
    )
    if numeric_boundaries:
        prompt = NUMERIC_BOUNDARY_SYSTEM_PROMPT
    return (
        prompt + "\n" + json.dumps(QuestionAssessmentDraft.model_json_schema(), ensure_ascii=False)
    )


def resolve_question_assessment(
    content: str,
    requirement: RequirementInput,
    catalog: dict[str, CitationInput],
    *,
    compact_missing_information: bool = False,
    expanded_missing_information: bool = False,
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
    return resolve_assessment(
        json.dumps(value, ensure_ascii=False),
        requirement,
        catalog,
        compact_missing_information=compact_missing_information,
        expanded_missing_information=expanded_missing_information,
    )
