import copy
import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from enterprise_doc_core.presales.errors import OutputContractError, PresalesError
from enterprise_doc_core.presales.question_assessment import offer_requirement_parts
from enterprise_doc_core.presales.question_routing import (
    resolve_routed_question,
    routed_response_format,
)
from enterprise_doc_core.presales.schemas import CitationInput, RequirementInput


def fixture():
    requirement = RequirementInput(key="FACT1", text="密码政策要求是什么\uff1f")
    catalog = {
        "policy": CitationInput(
            chunk_id=uuid4(),
            document_version_id=uuid4(),
            excerpt="Passwords must have at least eight characters.",
        )
    }
    value = {
        "result": {
            "mode": "facts",
            "requirementPartId": offer_requirement_parts(requirement, whole_requirement=True)[
                0
            ].requirement_part_id,
            "findings": [
                {
                    "kind": "known",
                    "requestedEvidence": "description",
                    "answer": "密码政策要求至少八个字符。",
                    "evidence": [{"citationId": "policy", "role": "policy"}],
                }
            ],
            "status": "supported",
            "conclusion": "资料明确说明密码政策。",
        }
    }
    return requirement, catalog, value


def resolve(value, requirement, catalog):
    return resolve_routed_question(json.dumps(value), requirement, catalog)


def test_policy_description_has_citations_and_cannot_create_prerequisites():
    requirement, catalog, value = fixture()
    draft = resolve(value, requirement, catalog)
    assert draft.prerequisites == []
    assert draft.status == "supported"
    assert "至少八个字符" in draft.answer
    assert draft.citations == [catalog["policy"]]
    assert draft.missing_information == []
    for field in ("rules", "assessments", "prerequisites"):
        invalid = copy.deepcopy(value)
        invalid["result"][field] = []
        with pytest.raises(ValidationError):
            resolve(invalid, requirement, catalog)


def test_policy_only_execution_assertion_is_rejected_without_relabeling():
    requirement, catalog, value = fixture()
    value["result"]["findings"][0]["requestedEvidence"] = "execution"
    original = copy.deepcopy(value)
    with pytest.raises(ValueError, match="state evidence"):
        resolve(value, requirement, catalog)
    assert value == original


def test_unknown_execution_keeps_policy_context_and_specific_gap():
    requirement, catalog, value = fixture()
    value["result"]["findings"].append(
        {
            "kind": "unknown",
            "requestedEvidence": "execution",
            "answer": "政策没有说明终端是否均已纳管\uff0c当前无法确认。",
            "context": [{"citationId": "policy", "role": "policy"}],
            "question": "哪些终端已纳管并执行这些要求\uff1f",
        }
    )
    value["result"]["status"] = "insufficient_evidence"
    draft = resolve(value, requirement, catalog)
    assert not draft.prerequisites
    assert draft.missing_information == ["哪些终端已纳管并执行这些要求\uff1f"]
    assert "当前无法确认" in draft.answer
    value["result"]["status"] = "supported"
    with pytest.raises(ValueError, match="unknown"):
        resolve(value, requirement, catalog)


@pytest.mark.parametrize("fault", ["citation", "language", "part", "size", "conditional", "gap"])
def test_routed_facts_keep_public_and_question_guards(fault):
    requirement, catalog, value = fixture()
    result = value["result"]
    if fault == "citation":
        result["findings"][0]["evidence"][0]["citationId"] = "foreign"
    elif fault == "language":
        result["findings"][0]["answer"] = "Policy specifies eight characters."
    elif fault == "part":
        result["requirementPartId"] = "foreign"
    elif fault == "size":
        result["findings"] *= 5
        for fact in result["findings"]:
            fact["answer"] = "答" * 990
    elif fault == "conditional":
        result["status"] = "conditional"
    else:
        result["findings"] = [
            {
                "kind": "unknown",
                "requestedEvidence": "execution",
                "answer": "未知。",
                "context": [],
                "question": "",
            }
        ]
    with pytest.raises((ValueError, ValidationError, PresalesError, OutputContractError)):
        resolve(value, requirement, catalog)


def test_conditions_reuse_unknown_obligation_without_turning_it_into_failure():
    requirement, catalog, value = fixture()
    catalog["rule"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="服务启用前必须完成校验。"
    )
    value["result"] = {
        "mode": "conditions",
        "draft": {
            "rules": [{"proposition": "完成校验", "requiredBy": [{"spanId": "rule_s1"}]}],
            "assessments": [
                {
                    "ruleIndex": 0,
                    "state": "unknown",
                    "uncertainty": "missing",
                    "observations": [],
                    "summary": "实际结果未知。",
                    "nextAction": "校验实际是否完成\uff1f",
                }
            ],
            "responses": [
                {
                    "requirementPartId": value["result"]["requirementPartId"],
                    "answer": "条款要求完成校验\uff0c现有资料不足以判断履约情况。",
                    "citations": [{"citationId": "rule"}],
                    "missingInformation": [],
                }
            ],
            "status": "conditional",
            "conclusion": "启用前仍需确认校验结果。",
        },
    }
    draft = resolve(value, requirement, catalog)
    assert draft.prerequisites[0].state == "unknown"
    assert "校验实际是否完成\uff1f" in draft.missing_information


def test_routing_schema_is_strict_with_distinct_business_fields():
    _, _, value = fixture()
    schema = routed_response_format()["json_schema"]["schema"]
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


def test_explicit_execution_record_can_answer_execution_question():
    requirement, catalog, value = fixture()
    catalog["state"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="本月在管的全部终端均已完成加密。"
    )
    value["result"]["findings"] = [
        {
            "kind": "known",
            "requestedEvidence": "execution",
            "answer": "本月在管终端均已加密。",
            "evidence": [{"citationId": "state", "role": "state"}],
        }
    ]
    draft = resolve(value, requirement, catalog)
    assert draft.status == "supported"
    assert draft.citations == [catalog["state"]]
    assert draft.prerequisites == []


def test_each_conflicting_finding_requires_its_own_opposing_source_versions():
    requirement, catalog, value = fixture()
    catalog["second"] = CitationInput(
        chunk_id=uuid4(), document_version_id=uuid4(), excerpt="密码至少十二个字符。"
    )
    value["result"]["findings"] = [
        {
            "kind": "conflict",
            "requestedEvidence": "description",
            "answer": "两份适用政策的最低长度矛盾。",
            "evidence": [
                {"citationId": "policy", "role": "policy"},
                {"citationId": "second", "role": "policy"},
            ],
            "question": "当前应适用哪份政策\uff1f",
        }
    ]
    value["result"]["status"] = "conflicting_evidence"
    draft = resolve(value, requirement, catalog)
    assert draft.status == "conflicting_evidence"
    assert len(draft.citations) == 2
    value["result"]["findings"][0]["evidence"][1]["citationId"] = "policy"
    with pytest.raises(ValueError, match="distinct source versions"):
        resolve(value, requirement, catalog)


def test_role_labels_are_not_an_entailment_or_routing_oracle():
    requirement, catalog, value = fixture()
    fact = value["result"]["findings"][0]
    fact["requestedEvidence"] = "execution"
    fact["evidence"][0]["role"] = "state"
    # A wrong semantic label is structurally legal. Keep it visible in raw reports;
    # do not pretend that the parser can prove a policy is an execution record.
    draft = resolve(value, requirement, catalog)
    assert draft.citations == [catalog["policy"]]


def test_unknown_fact_can_have_no_source_without_inventing_one():
    requirement, catalog, value = fixture()
    value["result"]["findings"] = [
        {
            "kind": "unknown",
            "requestedEvidence": "execution",
            "answer": "现有资料没有记录实际覆盖率。",
            "context": [],
            "question": "当前实际覆盖率是多少\uff1f",
        }
    ]
    value["result"]["status"] = "insufficient_evidence"
    draft = resolve(value, requirement, catalog)
    assert not draft.citations and not draft.prerequisites
    assert draft.missing_information == ["当前实际覆盖率是多少\uff1f"]
