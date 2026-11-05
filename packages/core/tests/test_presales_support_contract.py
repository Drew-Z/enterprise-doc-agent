import itertools
import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.output_contract import strict_response_format
from enterprise_doc_core.presales.schemas import CitationInput
from enterprise_doc_core.presales.support_contract import (
    ConstrainedBasisDraft,
    constrained_response_format,
    resolve_constrained_basis,
)


def quote(reference="cite_rule_1", text="启用前须完成域名校验。"):
    return {"citationId": reference, "text": text}


def item(uncertainty="none", positive=True, negative=False, unconfirmed=False):
    return {
        "proposition": "域名校验已完成",
        "definition": [quote()],
        "uncertainty": uncertainty,
        "positive": [quote(text="域名校验已完成。")] if positive else [],
        "negative": [quote("cite_rule_2", "域名校验尚未完成。")] if negative else [],
        "unconfirmed": [quote("cite_rule_3", "域名校验状态未记录。")] if unconfirmed else [],
    }


def draft(prerequisite):
    return {
        "prerequisites": [prerequisite],
        "status": "conditional",
        "answer": "按资料逐项判断业务前提。",
        "missingInformation": ["请确认域名校验的当前状态及适用资料。"],
        "citations": [],
    }


@pytest.mark.parametrize(
    "uncertainty,positive,negative,unconfirmed",
    list(
        itertools.product(
            ["none", "missing", "conflict"], [False, True], [False, True], [False, True]
        )
    ),
)
def test_schema_and_parser_accept_only_the_complete_support_truth_table(
    uncertainty, positive, negative, unconfirmed
):
    value = draft(item(uncertainty, positive, negative, unconfirmed))
    allowed = (
        (uncertainty == "none" and positive != negative and not unconfirmed)
        or (uncertainty == "missing" and not positive and not negative)
        or (uncertainty == "conflict" and positive and negative and not unconfirmed)
    )
    schema = constrained_response_format()["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    # v18's historical schema only constrained types; preserve that exact distinction.
    Draft202012Validator(strict_response_format()["json_schema"]["schema"]).validate(value)
    if allowed:
        validator.validate(value)
        ConstrainedBasisDraft.model_validate(value)
    else:
        with pytest.raises(SchemaError):
            validator.validate(value)
        with pytest.raises(ValidationError):
            ConstrainedBasisDraft.model_validate(value)


@pytest.mark.parametrize(
    "uncertainty,positive,negative,unconfirmed,status,state",
    [
        ("none", True, False, False, "supported", "met"),
        ("none", False, True, False, "conditional", "unmet"),
        ("missing", False, False, False, "conditional", "unknown"),
        ("missing", False, False, True, "conditional", "unknown"),
        ("conflict", True, True, False, "conflicting_evidence", "unknown"),
    ],
)
def test_all_valid_states_keep_literal_evidence_and_public_projection(
    uncertainty, positive, negative, unconfirmed, status, state
):
    catalog = {
        f"cite_rule_{index}": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt=text
        )
        for index, text in enumerate(
            [
                "启用前须完成域名校验。域名校验已完成。",
                "域名校验尚未完成。",
                "域名校验状态未记录。",
            ],
            start=1,
        )
    }
    selected = item(uncertainty, positive, negative, unconfirmed)
    value = {**draft(selected), "status": status}
    actual = resolve_constrained_basis(json.dumps(value), catalog)
    assert actual.status == status
    assert len(actual.prerequisites) == 1 and actual.prerequisites[0].state == state
    assert actual.prerequisites[0].condition == "核验事项\uff1a域名校验已完成"
    assert all(
        c == catalog[key]
        for c, key in zip(
            actual.citations,
            dict.fromkeys(
                q["citationId"]
                for field in ["definition", "unconfirmed", "positive", "negative"]
                for q in selected[field]
            ),
            strict=True,
        )
    )


@pytest.mark.parametrize("fault", ["quote", "foreign", "english", "gap", "size"])
def test_valid_support_branch_does_not_bypass_existing_business_and_source_guards(fault):
    catalog = {
        "cite_rule_1": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt="启用前须完成域名校验。"
        )
    }
    value = draft(item("missing", False, False, False))
    if fault == "quote":
        value["prerequisites"][0]["definition"][0]["text"] = "必须先购买模块。"
    elif fault == "foreign":
        value["citations"] = [{"citationId": "not-offered"}]
    elif fault == "english":
        value["answer"] = "Needs confirmation."
    elif fault == "gap":
        value["missingInformation"] = []
    else:
        value["answer"] = "待" * 4001
    with pytest.raises((ValueError, PresalesError)):
        resolve_constrained_basis(json.dumps(value), catalog)
