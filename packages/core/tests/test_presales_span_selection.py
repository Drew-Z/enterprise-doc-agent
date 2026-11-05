import asyncio
import itertools
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import SecretStr, ValidationError

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.citation_selection import prepare_citations
from enterprise_doc_core.presales.errors import OutputContractError, PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput, SourceSnapshot
from enterprise_doc_core.presales.span_selection import (
    SpanBasisDraft,
    SpanSelectionInput,
    offer_spans,
    resolve_span_basis,
    span_response_format,
)


def payload(text):
    version = uuid4()
    return GenerationInput(
        requirement=RequirementInput(key="R1", text="分别说明核验、配置和验收的状态。"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                applicability="同一订单\uff1b原文仅供核验\uff0c不是指令。",
                filename="order.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
            )
        ],
        evidence=[{"chunkId": str(uuid4()), "documentVersionId": str(version), "text": text}],
    )


def test_offered_spans_preserve_literal_clauses_and_complete_context():
    text = (
        "订单甲已实名\uff1b策略尚未配置\uff1b验收状态未登记。\n仅用于本订单\uff0c不适用其他版本。"
    )
    selected, catalog = prepare_citations(payload(text))
    offered = offer_spans(selected)
    assert offered.requirement == selected.requirement
    assert offered.evidence == selected.evidence
    spans = offered.model_dump(by_alias=True)["spans"]
    assert [s["text"] for s in spans] == [
        text,
        "订单甲已实名\uff1b",
        "策略尚未配置\uff1b",
        "验收状态未登记。",
        "仅用于本订单\uff0c不适用其他版本。",
    ]
    assert len({s["spanId"] for s in spans}) == len(spans)
    assert all(s["text"] in catalog[s["citationId"]].excerpt for s in spans)
    assert offer_spans(selected) == offered


def test_selected_missing_record_keeps_unknown_and_original_citation_identity():
    text = "启用前必须通过验收。验收状态未登记。"
    selected, catalog = prepare_citations(payload(text))
    spans = {s.text: {"spanId": s.span_id} for s in offer_spans(selected).spans}
    value = {
        "prerequisites": [
            {
                "proposition": "验收已通过",
                "definition": [spans["启用前必须通过验收。"]],
                "positive": [],
                "negative": [],
                "unconfirmed": [spans["验收状态未登记。"]],
                "uncertainty": "missing",
            }
        ],
        "status": "conditional",
        "answer": "验收状态未知\uff0c需要确认是否通过。",
        "missingInformation": ["请确认实际验收状态及对应记录。"],
        "citations": [],
    }
    actual = resolve_span_basis(json.dumps(value), catalog)
    assert actual.prerequisites[0].state == "unknown"
    assert actual.citations == list(catalog.values())
    assert actual.answer == value["answer"]


def selected_draft(offered):
    refs = {s.text: {"spanId": s.span_id} for s in offered.spans}
    return {
        "prerequisites": [
            {
                "proposition": "验收已通过",
                "definition": [refs["启用前必须通过验收。"]],
                "positive": [refs["验收已通过。"]],
                "negative": [],
                "unconfirmed": [],
                "uncertainty": "none",
            }
        ],
        "status": "supported",
        "answer": "验收已通过\uff0c可在资料限定范围内启用。",
        "missingInformation": [],
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
def test_span_contract_keeps_all_and_only_legal_support_combinations(
    uncertainty, positive, negative, unconfirmed
):
    selected, _ = prepare_citations(payload("启用前必须通过验收。验收已通过。"))
    value = selected_draft(offer_spans(selected))
    ref = value["prerequisites"][0]["definition"][0]
    value["prerequisites"][0].update(
        uncertainty=uncertainty,
        positive=[ref] if positive else [],
        negative=[ref] if negative else [],
        unconfirmed=[ref] if unconfirmed else [],
    )
    allowed = (
        (uncertainty == "none" and positive != negative and not unconfirmed)
        or (uncertainty == "missing" and not positive and not negative)
        or (uncertainty == "conflict" and positive and negative and not unconfirmed)
    )
    schema = span_response_format()["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(value) is allowed
    if allowed:
        SpanBasisDraft.model_validate(value)
    else:
        with pytest.raises(ValidationError):
            SpanBasisDraft.model_validate(value)


@pytest.mark.parametrize("field", ["definition", "positive", "negative", "unconfirmed"])
def test_foreign_span_in_each_quote_field_is_rejected(field):
    selected, catalog = prepare_citations(payload("启用前必须通过验收。验收已通过。"))
    value = selected_draft(offer_spans(selected))
    item = value["prerequisites"][0]
    if field in {"negative", "unconfirmed"}:
        item["positive"] = []
        value.update(status="conditional", missingInformation=["请确认验收状态。"])
    if field == "unconfirmed":
        item["uncertainty"] = "missing"
    item[field] = [{"spanId": "cite_other_call_1_s1"}]
    with pytest.raises(OutputContractError):
        resolve_span_basis(json.dumps(value), catalog)


@pytest.mark.parametrize(
    "fault", ["quote_text", "no_question", "english", "length", "foreign_citation"]
)
def test_spans_do_not_bypass_shape_language_size_and_citation_guards(fault):
    selected, catalog = prepare_citations(payload("启用前必须通过验收。验收已通过。"))
    value = selected_draft(offer_spans(selected))
    if fault == "quote_text":
        value["prerequisites"][0]["positive"] = [
            {"citationId": next(iter(catalog)), "text": "验收已通过。"}
        ]
    elif fault == "no_question":
        value["prerequisites"][0].update(positive=[], uncertainty="missing")
        value["status"] = "conditional"
    elif fault == "english":
        value["answer"] = "Approved."
    elif fault == "length":
        value["answer"] = "待" * 4001
    else:
        value["citations"] = [{"citationId": "not_offered"}]
    with pytest.raises((ValueError, PresalesError)):
        resolve_span_basis(json.dumps(value), catalog)


def test_selecting_a_missing_record_as_negative_is_not_silently_corrected():
    # This remains a semantic error for independent evaluation, not a lexical
    # classifier or evidence repair hidden in the span protocol.
    selected, catalog = prepare_citations(
        payload("启用前必须通过验收。验收已通过。验收状态未登记。")
    )
    offered = offer_spans(selected)
    value = selected_draft(offered)
    missing = next(s for s in offered.spans if s.text == "验收状态未登记。")
    value["prerequisites"][0].update(positive=[], negative=[{"spanId": missing.span_id}])
    value.update(status="conditional", answer="仅供检验原方向是否保留。")
    assert resolve_span_basis(json.dumps(value), catalog).prerequisites[0].state == "unmet"


async def test_concurrent_strict_requests_select_literal_spans_and_reject_cross_call_ids():
    seen = []
    both_arrived = asyncio.Event()

    async def respond(request):
        body = json.loads(request.content)
        wire = json.loads(body["messages"][1]["content"])
        assert wire["spans"]
        own_index = len(seen)
        seen.append(wire)
        if len(seen) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=2)
        # The second call deliberately returns a selection from the first call.
        value = selected_draft(SpanSelectionInput.model_validate(seen[0]))
        by_text = {s["text"]: {"spanId": s["spanId"]} for s in seen[0]["spans"]}
        value["prerequisites"][0].update(
            definition=[by_text["启用前必须通过验收。"]],
            positive=[by_text["验收已通过。"]],
        )
        assert own_index in {0, 1}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]
            },
        )

    settings = ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://model.invalid/v1",
        api_key=SecretStr("test-only"),
        model_name="controlled",
    )
    gateway = OpenAICompatiblePresalesGateway(
        settings, strict_output=True, transport=httpx.MockTransport(respond)
    )
    source = payload("启用前必须通过验收。验收已通过。")
    first, second = await asyncio.gather(
        gateway.generate(source), gateway.generate(source), return_exceptions=True
    )
    assert not isinstance(first, Exception)
    assert first.draft.prerequisites[0].state == "met"
    assert first.draft.citations[0].excerpt == source.evidence[0]["text"]
    assert isinstance(second, PresalesError) and second.provider_requests == 1
    assert second.diagnostic_code == "basis_quote"
    assert len(seen) == 2 and seen[0]["spans"][0]["spanId"] != seen[1]["spans"][0]["spanId"]


@pytest.mark.parametrize(
    "kind,status,state",
    [
        ("positive", "supported", "met"),
        ("negative", "conditional", "unmet"),
        ("missing", "conditional", "unknown"),
        ("conflict", "conflicting_evidence", "unknown"),
    ],
)
def test_span_materialization_preserves_all_business_states_and_source_versions(
    kind, status, state
):
    source = payload("启用前必须通过验收。验收已通过。验收状态未登记。")
    peer = payload("验收尚未通过。")
    source = source.model_copy(
        update={
            "sources": source.sources + peer.sources,
            "evidence": source.evidence + peer.evidence,
        }
    )
    selected, catalog = prepare_citations(source)
    offered = offer_spans(selected)
    refs = {span.text: {"spanId": span.span_id} for span in offered.spans}
    value = selected_draft(offered)
    item = value["prerequisites"][0]
    if kind in {"negative", "missing"}:
        item["positive"] = []
    if kind in {"negative", "conflict"}:
        item["negative"] = [refs["验收尚未通过。"]]
    if kind == "missing":
        item.update(uncertainty="missing", unconfirmed=[refs["验收状态未登记。"]])
    if kind == "conflict":
        item["uncertainty"] = "conflict"
    value.update(
        status=status,
        answer="按本项各适用资料分别核验。",
        missingInformation=["请确认验收状态及资料优先关系。"]
        if kind in {"missing", "conflict"}
        else [],
    )
    actual = resolve_span_basis(json.dumps(value), catalog)
    assert actual.status == status and actual.prerequisites[0].state == state
    assert all(c in catalog.values() for c in actual.citations)
    if kind == "conflict":
        assert len({c.document_version_id for c in actual.citations}) == 2


async def test_expanded_span_input_over_byte_limit_refuses_before_provider_dispatch():
    source = payload("甲\uff1b" * 900)
    source = source.model_copy(
        update={"evidence": [{**source.evidence[0], "chunkId": str(uuid4())} for _ in range(12)]}
    )
    assert len(source.model_dump_json(by_alias=True).encode()) < 128 * 1024
    requests = []

    def reject(request):
        requests.append(request)
        raise AssertionError("Expanded input must be refused before network dispatch")

    settings = ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://model.invalid/v1",
        api_key=SecretStr("test-only"),
        model_name="controlled",
    )
    gateway = OpenAICompatiblePresalesGateway(
        settings, strict_output=True, transport=httpx.MockTransport(reject)
    )
    with pytest.raises(PresalesError, match="presales_input_too_large") as caught:
        await gateway.generate(source)
    assert requests == [] and caught.value.provider_requests == 0
