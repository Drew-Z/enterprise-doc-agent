from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.evidence_selection import resolve_evidence_selection
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    GenerationInput,
    RequirementInput,
    SourceSnapshot,
)


def payload() -> GenerationInput:
    version = uuid4()
    return GenerationInput(
        requirement=RequirementInput(key="R5", text="能否启用归档?"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                applicability="本订单",
                filename="order.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
            )
        ],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(version),
                "text": "归档须采购、配置并验收。已采购归档。配置尚未完成。验收状态未登记。",
            }
        ],
    )


def item(reference, uncertainty="missing", positive=None, negative=None):
    return {
        "proposition": "该前提已经满足。",
        "uncertainty": uncertainty,
        "positive": [{"citationId": reference, "text": positive}] if positive else [],
        "negative": [{"citationId": reference, "text": negative}] if negative else [],
        "condition": "需确认验收情况。",
        "citations": [{"citationId": reference}],
    }


async def invoke(make_items, *, finish="stop", message_extra=None, source_text=None):
    calls = []

    async def respond(request):
        sent = json.loads(request.content)
        calls.append(sent)
        evidence = json.loads(sent["messages"][1]["content"])["evidence"]
        selected = {
            "status": "conditional",
            "answer": "采购已满足。配置待完成。验收待确认。",
            "prerequisites": make_items(evidence[0]["citationId"]),
            "missingInformation": ["请确认验收是否通过。"],
            "citations": [],
        }
        return httpx.Response(
            200,
            headers={"x-oneapi-request-id": "request-evidence"},
            json={
                "id": "response-evidence",
                "model": "controlled",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish,
                        "message": {"content": json.dumps(selected), **(message_extra or {})},
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 13, "total_tokens": 34},
            },
        )

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.example/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        transport=httpx.MockTransport(respond),
    )
    try:
        source = payload()
        if source_text is not None:
            source.evidence[0]["text"] = source_text
        return await gateway.generate(source)
    finally:
        assert len(calls) == 1


async def test_evidence_directions_derive_states_and_keep_public_projection():
    def items(ref):
        purchased = item(ref, "none", positive="已采购归档。")
        purchased["condition"] = "已采购归档。"
        unfinished = item(ref, "none", negative="配置尚未完成。")
        unfinished["condition"] = "需完成配置。"
        return [purchased, unfinished, item(ref)]

    result = await invoke(items)
    assert [p.state for p in result.draft.prerequisites] == ["met", "unmet", "unknown"]
    assert result.draft.conditions == ["需完成配置。", "需确认验收情况。"]
    assert all(p.citation_indexes == [0] for p in result.draft.prerequisites)
    assert "proposition" not in result.draft.model_dump_json()
    assert result.provider_response_id == "response-evidence"
    assert result.provider_request_id == "request-evidence"
    assert result.usage == {"prompt_tokens": 21, "completion_tokens": 13, "total_tokens": 34}


async def test_conflicting_literal_support_stays_unknown():
    result = await invoke(
        lambda ref: [item(ref, "conflict", "验收已通过。", "验收未通过。")],
        source_text="记录甲称验收已通过。记录乙称验收未通过。两份记录效力相同。",
    )
    assert result.draft.prerequisites[0].state == "unknown"
    assert result.draft.conditions == ["需确认验收情况。"]


@pytest.mark.parametrize("wrong_source", [False, True])
def test_literal_support_is_bound_to_its_own_fragment_and_joined_to_context(wrong_source):
    catalog = {
        "context": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt="必须完成配置。"
        ),
        "status": CitationInput(
            chunk_id=uuid4(), document_version_id=uuid4(), excerpt="配置尚未完成。"
        ),
    }
    selected = item("context", "none", negative="配置尚未完成。")
    selected["negative"][0]["citationId"] = "context" if wrong_source else "status"
    content = json.dumps(
        {"status": "conditional", "answer": "需完成配置。", "prerequisites": [selected]}
    )
    if wrong_source:
        with pytest.raises(ValueError, match="unsupported literal quotation"):
            resolve_evidence_selection(content, catalog)
    else:
        draft = resolve_evidence_selection(content, catalog)
        assert draft.citations == [catalog["context"], catalog["status"]]
        assert draft.prerequisites[0].citation_indexes == [0, 1]
        assert draft.prerequisites[0].state == "unmet"


@pytest.mark.parametrize(
    "uncertainty,positive,negative",
    [
        ("none", None, None),
        ("none", "已采购归档。", "配置尚未完成。"),
        ("missing", "已采购归档。", None),
        ("missing", None, "配置尚未完成。"),
        ("conflict", None, None),
        ("conflict", "已采购归档。", None),
        ("conflict", None, "配置尚未完成。"),
    ],
)
async def test_inconsistent_evidence_rejects_with_accounting(uncertainty, positive, negative):
    with pytest.raises(PresalesError) as caught:
        await invoke(lambda ref: [item(ref, uncertainty, positive, negative)])
    error = caught.value
    assert error.code == "presales_invalid_model_output"
    assert error.provider_requests == 1
    assert error.provider_response_id == "response-evidence"
    assert error.provider_request_id == "request-evidence"
    assert error.usage["total_tokens"] == 34


@pytest.mark.parametrize("mode", ["invented", "foreign", "state", "duplicate"])
async def test_untrusted_evidence_is_not_repaired(mode):
    def items(ref):
        value = item(ref)
        if mode in {"invented", "foreign"}:
            value = item(ref, "none", negative="配置尚未完成。")
            if mode == "invented":
                value["negative"][0]["text"] = "验收明确未通过。"
            else:
                value["negative"][0]["citationId"] = "cite_previous_request_1"
        elif mode == "state":
            value["state"] = "met"
        else:
            value["citations"] *= 2
        return [value]

    with pytest.raises(PresalesError) as caught:
        await invoke(items)
    assert caught.value.code == "presales_invalid_model_output"
    assert caught.value.usage["total_tokens"] == 34


@pytest.mark.parametrize(
    "finish,extra",
    [
        ("length", {}),
        ("stop", {"refusal": "no"}),
        ("stop", {"tool_calls": [{"id": "tool"}]}),
    ],
)
async def test_completion_guards_survive_evidence_protocol(finish, extra):
    with pytest.raises(PresalesError) as caught:
        await invoke(lambda ref: [item(ref)], finish=finish, message_extra=extra)
    assert caught.value.code == "presales_invalid_model_output"
    assert caught.value.provider_response_id == "response-evidence"
    assert caught.value.usage["total_tokens"] == 34
