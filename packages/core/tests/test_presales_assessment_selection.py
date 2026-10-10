import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from enterprise_doc_core.config import ModelSettings
from enterprise_doc_core.presales.assessment_selection import (
    PROTOCOL_VERSION,
    AssessmentDraft,
    assessment_response_format,
    assessment_system_message,
    resolve_assessment,
)
from enterprise_doc_core.presales.citation_selection import prepare_citations
from enterprise_doc_core.presales.errors import OutputContractError, PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    GenerationInput,
    RequirementInput,
    SourceSnapshot,
)
from enterprise_doc_core.presales.span_selection import offer_spans


def fixture():
    version = uuid4()
    requirement = RequirementInput(key="A1", text="说明启用状态。说明下一步事项。")
    source = GenerationInput(
        requirement=requirement,
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="assessment.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
                applicability="同一订单及日期。",
            )
        ],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(version),
                "text": "启用前必须完成配置。配置已经完成。启用前必须通过校验。校验结果未登记。",
            }
        ],
    )
    selected, catalog = prepare_citations(source)
    refs = {s.text: {"spanId": s.span_id} for s in offer_spans(selected).spans}
    value = {
        "rules": [
            {"proposition": "配置已完成", "requiredBy": [refs["启用前必须完成配置。"]]},
            {"proposition": "校验已通过", "requiredBy": [refs["启用前必须通过校验。"]]},
        ],
        "assessments": [
            {
                "ruleIndex": 0,
                "state": "met",
                "evidence": [refs["配置已经完成。"]],
                "summary": "配置已经完成。",
                "nextAction": None,
            },
            {
                "ruleIndex": 1,
                "state": "unknown",
                "uncertainty": "missing",
                "observations": [refs["校验结果未登记。"]],
                "summary": "校验实际通过情况未知。",
                "nextAction": "请确认校验是否通过及其结果记录。",
            },
        ],
        "responses": [
            {
                "requirementText": "说明启用状态。",
                "answer": "配置已完成\uff0c校验通过情况尚待核实。",
                "citations": [{"citationId": next(iter(catalog))}],
                "missingInformation": [],
            },
            {
                "requirementText": "说明下一步事项。",
                "answer": "只需核实校验通过情况\uff0c无须重复配置。",
                "citations": [],
                "missingInformation": ["请确认校验是否通过及其结果记录。"],
            },
        ],
        "status": "conditional",
        "conclusion": "已有能力路径\uff0c但校验状态仍待确认。",
    }
    return requirement, catalog, value


def test_rule_assessment_and_each_requested_response_survive_projection():
    requirement, catalog, value = fixture()
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert [p.state for p in draft.prerequisites] == ["met", "unknown"]
    assert draft.missing_information == ["请确认校验是否通过及其结果记录。"]
    assert draft.citations == list(catalog.values())
    for part in value["responses"]:
        assert part["requirementText"] in draft.answer
        assert part["answer"] in draft.answer
    assert value["assessments"][1]["nextAction"] in draft.answer


def test_each_answer_keeps_its_own_information_gap_next_to_the_question():
    requirement, catalog, value = fixture()
    question = "请补充该事项适用的维护窗口。"
    value["responses"][0]["missingInformation"] = [question]
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert question in draft.missing_information
    assert draft.answer.index(question) < draft.answer.index("说明下一步事项。")


@pytest.mark.parametrize("fault", ["omitted", "duplicated", "foreign", "boolean", "negative"])
def test_rule_links_reject_missing_duplicate_foreign_or_noninteger_assessments(fault):
    requirement, catalog, value = fixture()
    if fault == "omitted":
        value["assessments"].pop()
    else:
        value["assessments"][1]["ruleIndex"] = {
            "duplicated": 0,
            "foreign": 2,
            "boolean": True,
            "negative": -1,
        }[fault]
    with pytest.raises(ValueError):
        resolve_assessment(json.dumps(value), requirement, catalog)


def test_assessment_order_is_bound_by_rule_index_instead_of_array_position():
    requirement, catalog, value = fixture()
    value["assessments"].reverse()
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert [p.state for p in draft.prerequisites] == ["met", "unknown"]


@pytest.mark.parametrize("fault", ["omitted", "repeated", "reversed", "invented", "gap"])
def test_response_coverage_rejects_changed_or_missing_requirement_text(fault):
    requirement, catalog, value = fixture()
    if fault == "omitted":
        value["responses"].pop()
    elif fault == "repeated":
        value["responses"][1]["requirementText"] = value["responses"][0]["requirementText"]
    elif fault == "reversed":
        value["responses"].reverse()
    elif fault == "invented":
        value["responses"][0]["requirementText"] = "虚构的问题。"
    else:
        value["responses"][0]["requirementText"] = "启用状态。"
    with pytest.raises(ValueError):
        resolve_assessment(json.dumps(value), requirement, catalog)


def test_requirement_coverage_preserves_english_and_allows_only_whitespace_between_parts():
    requirement, catalog, value = fixture()
    requirement = requirement.model_copy(update={"text": "  API status?\n\tNext steps?  "})
    value["responses"][0]["requirementText"] = "API status?"
    value["responses"][1]["requirementText"] = "Next steps?"
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert "API status?" in draft.answer and "Next steps?" in draft.answer


def test_answer_without_evidence_or_explicit_gap_is_refused():
    requirement, catalog, value = fixture()
    value["responses"][0]["citations"] = []
    with pytest.raises(ValueError, match="each answer requires"):
        resolve_assessment(json.dumps(value), requirement, catalog)


@pytest.mark.parametrize("branch", ["met", "unmet", "missing", "conflict"])
def test_schema_and_projection_keep_all_four_states_with_required_actions(branch):
    requirement, catalog, value = fixture()
    item = value["assessments"][1]
    catalog["completion"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="校验已经通过。"
    )
    catalog["peer"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="校验尚未通过。"
    )
    if branch == "met":
        item = {
            "ruleIndex": 1,
            "summary": "校验已经通过。",
            "state": "met",
            "evidence": [{"spanId": "completion_s1"}],
            "nextAction": None,
        }
    elif branch == "unmet":
        item = {
            "ruleIndex": 1,
            "summary": "校验尚未通过。",
            "state": "unmet",
            "evidence": [{"spanId": "peer_s1"}],
            "nextAction": "完成校验并取得通过结果。",
        }
    elif branch == "conflict":
        item = {
            "ruleIndex": 1,
            "state": "unknown",
            "uncertainty": "conflict",
            "positive": [{"spanId": "completion_s1"}],
            "negative": [{"spanId": "peer_s1"}],
            "summary": "两份资料对校验是否通过的记录相互矛盾。",
            "nextAction": "请澄清两份资料的实际状态和优先关系。",
        }
    value["assessments"][1] = item
    value["status"] = {
        "met": "supported",
        "unmet": "conditional",
        "missing": "conditional",
        "conflict": "conflicting_evidence",
    }[branch]
    schema = assessment_response_format()["json_schema"]["schema"]
    Draft202012Validator(schema).validate(value)
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert (
        draft.prerequisites[1].state
        == {"met": "met", "unmet": "unmet", "missing": "unknown", "conflict": "unknown"}[branch]
    )
    if branch != "met":
        assert item["nextAction"] in draft.answer
    if branch in {"missing", "conflict"}:
        assert item["nextAction"] in draft.missing_information


@pytest.mark.parametrize(
    "fault", ["met_action", "missing_action", "empty_action", "mixed_fields", "missing_evidence"]
)
def test_schema_refuses_missing_actions_or_contradictory_assessment_fields(fault):
    _, _, value = fixture()
    if fault == "met_action":
        value["assessments"][0]["nextAction"] = "请再次配置。"
    elif fault == "missing_action":
        value["assessments"][1].pop("nextAction")
    elif fault == "empty_action":
        value["assessments"][1]["nextAction"] = ""
    elif fault == "mixed_fields":
        value["assessments"][1]["evidence"] = value["assessments"][0]["evidence"]
    else:
        value["assessments"][0]["evidence"] = []
    assert not Draft202012Validator(assessment_response_format()["json_schema"]["schema"]).is_valid(
        value
    )
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate(value)


def test_whitespace_action_and_empty_rule_evidence_reject():
    requirement, catalog, value = fixture()
    value["assessments"][1]["nextAction"] = "  "
    with pytest.raises(ValidationError):
        resolve_assessment(json.dumps(value), requirement, catalog)
    _, _, value = fixture()
    value["rules"][0]["requiredBy"] = []
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate(value)


@pytest.mark.parametrize("field", ["rule", "met", "missing", "citation"])
def test_unknown_or_cross_call_source_identifiers_reject(field):
    requirement, catalog, value = fixture()
    if field == "rule":
        value["rules"][0]["requiredBy"] = [{"spanId": "foreign_s1"}]
    elif field == "met":
        value["assessments"][0]["evidence"] = [{"spanId": "foreign_s1"}]
    elif field == "missing":
        value["assessments"][1]["observations"] = [{"spanId": "foreign_s1"}]
    else:
        value["responses"][0]["citations"] = [{"citationId": "foreign"}]
    with pytest.raises((OutputContractError, PresalesError)):
        resolve_assessment(json.dumps(value), requirement, catalog)


@pytest.mark.parametrize(
    "field", ["conclusion", "proposition", "summary", "action", "answer", "gap"]
)
def test_chinese_rendering_labels_cannot_hide_english_only_model_prose(field):
    requirement, catalog, value = fixture()
    if field == "conclusion":
        value[field] = "Unknown."
    elif field == "proposition":
        value["rules"][0][field] = "Configuration complete."
    elif field == "summary":
        value["assessments"][0][field] = "Configuration complete."
    elif field == "action":
        value["assessments"][1]["nextAction"] = "Please confirm."
    elif field == "answer":
        value["responses"][0][field] = "Unknown."
    else:
        value["responses"][0]["missingInformation"] = ["Please confirm."]
    with pytest.raises(ValueError, match="Chinese"):
        resolve_assessment(json.dumps(value), requirement, catalog)


def test_public_size_overflow_rejects_without_truncating_valid_candidate_fields():
    requirement, catalog, value = fixture()
    for part in value["responses"]:
        part["answer"] = "答" * 2000
    AssessmentDraft.model_validate(value)
    with pytest.raises(ValidationError):
        resolve_assessment(json.dumps(value), requirement, catalog)


def test_missing_evidence_may_be_absent_but_still_requires_a_specific_question():
    requirement, catalog, value = fixture()
    value["assessments"][1]["observations"] = []
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[1].state == "unknown"
    assert value["assessments"][1]["nextAction"] in draft.missing_information


def test_a_response_need_not_invent_business_prerequisites():
    requirement, catalog, value = fixture()
    value.update(rules=[], assessments=[], status="insufficient_evidence")
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites == []
    assert draft.missing_information


def test_role_or_entailment_errors_are_not_hidden_by_a_lexical_classifier():
    requirement, catalog, value = fixture()
    # Both are semantic errors deliberately retained by the structural parser.
    value["rules"][0]["requiredBy"] = value["assessments"][0]["evidence"]
    missing = value["assessments"][1]
    value["assessments"][1] = {
        "ruleIndex": 1,
        "state": "unmet",
        "evidence": missing["observations"],
        "summary": "校验状态未登记\uff0c但模型误判为未通过。",
        "nextAction": "完成校验。",
    }
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[1].state == "unmet"
    assert "模型误判" in draft.answer


def test_same_source_span_can_legitimately_define_rule_and_completion():
    requirement, catalog, value = fixture()
    catalog["combined"] = CitationInput(
        chunk_id=uuid4(),
        document_version_id=uuid4(),
        excerpt="启用要求完成配置\uff0c本订单已完成该配置。",
    )
    ref = {"spanId": "combined_s1"}
    value["rules"][0]["requiredBy"] = [ref]
    value["assessments"][0]["evidence"] = [ref]
    draft = resolve_assessment(json.dumps(value), requirement, catalog)
    assert draft.prerequisites[0].state == "met"
    assert catalog["combined"] in draft.citations


def test_strict_schema_identity_is_separate_and_every_object_field_is_required():
    schema = assessment_response_format()["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    assert PROTOCOL_VERSION == "presales.assessment-candidate.v1"
    assert assessment_system_message().endswith(json.dumps(schema, ensure_ascii=False))
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate({"prerequisites": [], "answer": "旧版文本"})
    gateway = OpenAICompatiblePresalesGateway(ModelSettings())
    assert gateway.provenance["promptVersion"] == "presales.v15"
    assert (
        gateway.provenance["promptSha256"]
        == "318fc29ef2903cef5ad51a59163fad35ff855aca012bbae986e84a0fbb83d2ab"
    )
    strict = OpenAICompatiblePresalesGateway(ModelSettings(), strict_output=True)
    assert strict.provenance["promptVersion"] == "presales.v20"
    assert (
        strict.provenance["promptSha256"]
        == "b2edcfdc8b9f9296f629ace9bf9255b6e7f9b34e86b787ef392cdc265b517375"
    )
