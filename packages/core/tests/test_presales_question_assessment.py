import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from enterprise_doc_core.config import ModelSettings
from enterprise_doc_core.presales.assessment_selection import AssessmentDraft
from enterprise_doc_core.presales.citation_selection import prepare_citations
from enterprise_doc_core.presales.errors import OutputContractError, PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.question_assessment import (
    PROTOCOL_VERSION,
    QuestionAssessmentDraft,
    offer_question_assessment,
    offer_requirement_parts,
    question_assessment_response_format,
    question_assessment_system_message,
    resolve_question_assessment,
)
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    GenerationInput,
    RequirementInput,
    SourceSnapshot,
)
from enterprise_doc_core.presales.span_selection import offer_spans


def test_offered_parts_preserve_every_character_and_repeated_question_occurrence():
    requirement = RequirementInput(key="Q1", text="说明状态。\n\t说明下一步\uff1f 说明状态。")
    parts = offer_requirement_parts(requirement)
    assert [part.text for part in parts] == ["说明状态。\n", "\t说明下一步\uff1f", " 说明状态。"]
    assert "".join(part.text for part in parts) == requirement.text
    assert len({part.requirement_part_id for part in parts}) == 3
    assert parts == offer_requirement_parts(requirement)


def fixture():
    requirement = RequirementInput(key="Q1", text="说明启用状态。说明下一步。")
    catalog = {
        "rule": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt="启用前必须通过校验。"
        ),
        "state": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt="校验结果未登记。"
        ),
    }
    parts = offer_requirement_parts(requirement)
    value = {
        "rules": [
            {"proposition": "通过校验", "requiredBy": [{"spanId": "rule_s1"}]},
        ],
        "assessments": [
            {
                "ruleIndex": 0,
                "state": "unknown",
                "uncertainty": "missing",
                "observations": [{"spanId": "state_s1"}],
                "summary": "校验是否通过未知。",
                "nextAction": "确认校验实际结果。",
            }
        ],
        "responses": [
            {
                "requirementPartId": part.requirement_part_id,
                "answer": answer,
                "citations": [{"citationId": "state"}],
                "missingInformation": ["校验实际是否通过\uff1f"],
            }
            for part, answer in zip(
                parts, ["现有资料不能确认校验通过。", "请核实校验结果。"], strict=True
            )
        ],
        "status": "conditional",
        "conclusion": "启用前仍需核实校验结果。",
    }
    return requirement, catalog, value


def test_selected_ids_render_original_questions_and_keep_unknown_state_and_actions():
    requirement, catalog, value = fixture()
    draft = resolve_question_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[0].state == "unknown"
    assert "说明启用状态。" in draft.answer and "说明下一步。" in draft.answer
    assert "请核实校验结果。" in draft.answer
    assert "确认校验实际结果。" in draft.missing_information
    assert set(c.excerpt for c in draft.citations) == {"启用前必须通过校验。", "校验结果未登记。"}


def test_offering_keeps_full_requirement_source_context_and_existing_spans():
    requirement, _, _ = fixture()
    version = uuid4()
    source = GenerationInput(
        requirement=requirement,
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="input.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
                applicability="仅适用于当前订单\uff0c其他订单无效。",
            )
        ],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(version),
                "text": "启用前必须通过校验。校验结果未登记。",
            }
        ],
    )
    selected, _ = prepare_citations(source)
    spans = offer_spans(selected)
    offered = offer_question_assessment(spans)
    assert offered.model_dump(exclude={"requirement_parts"}) == spans.model_dump()
    assert offered.requirement_parts == offer_requirement_parts(requirement)
    assert "requirementParts" in offered.model_dump(by_alias=True)


@pytest.mark.parametrize(
    "text",
    [
        "逐字保留" * 500,
        "状态\uff1f" * 30 + "但仅限当前订单\uff0c其他订单除外。",
        "API v1.2 status?\r\n\tNext steps; Scope!",
        "第一行\n\n\t第二行\r\n第三行",
        "\uff1f\uff01\uff1b\n说明限定。",
        "无标点问题",
    ],
)
def test_bounded_parts_never_drop_or_rewrite_text(text):
    requirement = RequirementInput(key="Q1", text=text)
    parts = offer_requirement_parts(requirement)
    assert 1 <= len(parts) <= 12
    assert "".join(part.text for part in parts) == requirement.text
    assert all(part.text.strip() for part in parts)
    if text.startswith("状态\uff1f"):
        assert len(parts) == 12
        assert parts[-1].text.endswith("但仅限当前订单\uff0c其他订单除外。")


def test_offered_parts_are_immutable():
    requirement, _, _ = fixture()
    part = offer_requirement_parts(requirement)[0]
    with pytest.raises(ValidationError, match="frozen"):
        part.text = "替换问题"


@pytest.mark.parametrize("field", ["key", "text", "source_location"])
def test_changed_requirement_identity_cannot_reuse_previous_ids(field):
    requirement, catalog, value = fixture()
    changes = {"key": "Q2", "text": "说明上线状态。说明下一步。", "source_location": "Sheet2!A5"}
    other = RequirementInput.model_validate({**requirement.model_dump(), field: changes[field]})
    with pytest.raises(ValueError, match="every offered requirement part"):
        resolve_question_assessment(json.dumps(value), other, catalog)


@pytest.mark.parametrize("fault", ["omitted", "duplicate", "reversed", "foreign", "extra"])
def test_every_offered_question_requires_exactly_one_ordered_answer(fault):
    requirement, catalog, value = fixture()
    if fault == "omitted":
        value["responses"].pop()
    elif fault == "duplicate":
        value["responses"][1]["requirementPartId"] = value["responses"][0]["requirementPartId"]
    elif fault == "reversed":
        value["responses"].reverse()
    elif fault == "foreign":
        value["responses"][0]["requirementPartId"] = "foreign"
    else:
        value["responses"].append(value["responses"][0].copy())
    with pytest.raises(ValueError, match="every offered requirement part"):
        resolve_question_assessment(json.dumps(value), requirement, catalog)


@pytest.mark.parametrize("state", ["met", "unmet", "conflict", "missing"])
def test_legacy_assessment_states_and_actions_survive_question_materialization(state):
    requirement, catalog, value = fixture()
    catalog["positive"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="校验已经通过。"
    )
    catalog["negative"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="校验尚未通过。"
    )
    if state in {"met", "unmet"}:
        value["assessments"] = [
            {
                "ruleIndex": 0,
                "state": state,
                "evidence": [{"spanId": "positive_s1" if state == "met" else "negative_s1"}],
                "summary": "校验已有明确记录。",
                "nextAction": None if state == "met" else "完成校验。",
            }
        ]
        value["status"] = "supported" if state == "met" else "conditional"
    elif state == "conflict":
        value["assessments"] = [
            {
                "ruleIndex": 0,
                "state": "unknown",
                "uncertainty": "conflict",
                "positive": [{"spanId": "positive_s1"}],
                "negative": [{"spanId": "negative_s1"}],
                "summary": "同级记录矛盾。",
                "nextAction": "澄清两份记录的权威状态。",
            }
        ]
        value["status"] = "conflicting_evidence"
    draft = resolve_question_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[0].state == (state if state in {"met", "unmet"} else "unknown")
    action = value["assessments"][0]["nextAction"]
    assert (action or "无新增待办。") in draft.answer
    if state == "conflict":
        assert catalog["positive"] in draft.citations and catalog["negative"] in draft.citations


@pytest.mark.parametrize("fault", ["span", "citation", "gap", "language", "size", "rule"])
def test_existing_source_language_and_public_output_guards_still_reject(fault):
    requirement, catalog, value = fixture()
    if fault == "span":
        value["rules"][0]["requiredBy"] = [{"spanId": "foreign_s1"}]
    elif fault == "citation":
        value["responses"][0]["citations"] = [{"citationId": "foreign"}]
    elif fault == "gap":
        value["responses"][0].update(citations=[], missingInformation=[])
    elif fault == "language":
        value["responses"][0]["answer"] = "Unknown."
    elif fault == "size":
        for response in value["responses"]:
            response["answer"] = "答" * 2000
    else:
        value["assessments"][0]["ruleIndex"] = 1
    with pytest.raises((ValueError, OutputContractError, PresalesError)):
        resolve_question_assessment(json.dumps(value), requirement, catalog)


def test_ids_do_not_silently_repair_action_meaning_or_invent_semantic_approval():
    requirement, catalog, value = fixture()
    ambiguous_action = "补充登记通过结果\uff0c或确认是否已通过。"
    value["assessments"][0]["nextAction"] = ambiguous_action
    draft = resolve_question_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[0].state == "unknown"
    assert ambiguous_action in draft.answer and ambiguous_action in draft.missing_information
    # This mechanical success is deliberately not a semantic pass: review is still required.
    assert not hasattr(draft, "approved")


def test_new_output_contract_is_strict_and_does_not_accept_copied_question_text():
    requirement, catalog, value = fixture()
    schema = question_assessment_response_format()["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(schema)
    assert PROTOCOL_VERSION == "presales.question-assessment-candidate.v1"
    message = question_assessment_system_message()
    assert "requirementText" not in message
    assert "requirementPartId" in message
    assert message.endswith(json.dumps(schema, ensure_ascii=False))
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate(value)
    for response in value["responses"]:
        response["requirementText"] = requirement.text
    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ValidationError):
        resolve_question_assessment(json.dumps(value), requirement, catalog)
    for response in value["responses"]:
        del response["requirementPartId"]
    with pytest.raises(ValidationError):
        QuestionAssessmentDraft.model_validate(value)


def test_candidate_does_not_change_gateway_identities():
    for strict, version, digest in [
        (False, "presales.v15", "318fc29ef2903cef5ad51a59163fad35ff855aca012bbae986e84a0fbb83d2ab"),
        (True, "presales.v20", "b2edcfdc8b9f9296f629ace9bf9255b6e7f9b34e86b787ef392cdc265b517375"),
    ]:
        gateway = OpenAICompatiblePresalesGateway(ModelSettings(), strict_output=strict)
        assert gateway.provenance["promptVersion"] == version
        assert gateway.provenance["promptSha256"] == digest
