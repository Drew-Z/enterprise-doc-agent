from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.generation import resolve_draft
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput, SourceSnapshot


def evidence_payload(text: str) -> GenerationInput:
    source = SourceSnapshot(
        version_id=uuid4(),
        applicability="本订单",
        document_id=uuid4(),
        generation_id=uuid4(),
        filename="contract.txt",
        version_number=1,
        latest_version_number=1,
        content_sha256="a" * 64,
    )
    return GenerationInput(
        requirement=RequirementInput(key="R1", text="核对采购承诺"),
        sources=[source],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(source.version_id),
                "text": text,
                "filename": source.filename,
                "heading": "合同条款",
                "pageNumber": "2",
            }
        ],
    )


def gateway(transport: httpx.AsyncBaseTransport) -> OpenAICompatiblePresalesGateway:
    return OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.example/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled-citation-test",
        ),
        transport=transport,
    )


def model_response(citations: list[dict], **changes) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "controlled-selection",
            "model": "controlled-citation-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "supported",
                                "answer": "按原文核对。",
                                "citations": citations,
                                "prerequisites": [],
                                **changes,
                            },
                            ensure_ascii=False,
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


async def test_model_selects_reference_and_server_returns_exact_authorized_source() -> None:
    original = "本订单月度可用性保证为 99.95%。\n例外\uff1a计划维护须提前 48 小时通知。"
    payload = evidence_payload(original)
    snapshot = payload.model_dump()
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        envelope = json.loads(request.content)
        sent = json.loads(envelope["messages"][1]["content"])
        calls.append(sent)
        assert sent["requirement"] == payload.requirement.model_dump(mode="json", by_alias=True)
        assert sent["evidence"][0]["text"] == original
        assert "citationId" in sent["evidence"][0]
        return model_response([{"citationId": sent["evidence"][0]["citationId"]}])

    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert len(calls) == 1 and payload.model_dump() == snapshot
    assert output.draft.citations[0].excerpt == original
    assert output.draft.citations[0].document_version_id == payload.sources[0].version_id
    assert str(output.draft.citations[0].chunk_id) == payload.evidence[0]["chunkId"]
    assert output.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    tenant_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=output.draft.citations[0].chunk_id,
        tenant_id=tenant_id,
        document_version_id=payload.sources[0].version_id,
        generation_id=payload.sources[0].generation_id,
        text=original,
        source_filename="contract.txt",
        page_number=2,
        start_offset=20,
        end_offset=20 + len(original),
    )
    saved = resolve_draft(output.draft, (candidate,), payload.sources, tenant_id, [])
    assert saved.citations[0].excerpt == original and saved.citations[0].page_number == 2


async def test_model_view_keeps_source_scope_without_internal_identity_or_extra_metadata() -> None:
    payload = evidence_payload("旧条款仍适用于本订单。")
    payload.sources[0].latest_version_number = 2
    other = evidence_payload("新条款只适用于其他订单。")
    other.sources[0].version_number = other.sources[0].latest_version_number = 2
    other.sources[0].applicability = "其他订单"
    payload.sources.extend(other.sources)
    payload.evidence.extend(other.evidence)
    payload.evidence[0]["internalNote"] = "must-not-reach-provider"
    payload.evidence[0]["filename"] = "untrusted-duplicate-metadata.txt"

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert set(sent) == {"requirement", "evidence"}
        for index, item in enumerate(sent["evidence"]):
            assert set(item) == {"citationId", "text", "source", "heading", "pageNumber"}
            assert item["source"] == {
                "label": f"来源 {index + 1}",
                "filename": "contract.txt",
                "versionNumber": index + 1,
                "latestVersionNumber": 2,
                "applicability": "本订单" if index == 0 else "其他订单",
            }
            assert item["heading"] == "合同条款" and item["pageNumber"] == "2"
            assert item["text"] == payload.evidence[index]["text"]
        wire = json.dumps(sent)
        for item in payload.evidence:
            assert item["chunkId"] not in wire and item["documentVersionId"] not in wire
        assert "must-not-reach-provider" not in wire
        return model_response([{"citationId": sent["evidence"][0]["citationId"]}])

    result = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert result.draft.citations[0].document_version_id == payload.sources[0].version_id


async def test_prerequisites_resolve_to_compatible_public_draft() -> None:
    payload = evidence_payload("SSO 必须先采购。本订单尚未采购。")

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        reference = {"citationId": sent["evidence"][0]["citationId"]}
        return model_response(
            [reference],
            prerequisites=[
                {"condition": "需采购 SSO。", "state": "unmet", "citations": [reference]}
            ],
            status="conditional",
            answer="采购后方可启用。目前尚未采购。",
        )

    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert output.draft.status == "conditional"
    assert output.draft.conditions == ["需采购 SSO。"]
    assert output.draft.citations[0].excerpt == payload.evidence[0]["text"]
    assert "prerequisites" not in output.draft.model_dump()


@pytest.mark.parametrize("state", ["unmet", "unknown"])
async def test_supported_cannot_omit_outstanding_prerequisites(state: str) -> None:
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        reference = {"citationId": sent["evidence"][0]["citationId"]}
        calls.append(sent)
        return model_response(
            [reference],
            prerequisites=[{"condition": "需采购 SSO。", "state": state, "citations": [reference]}],
        )

    with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$") as error:
        await gateway(httpx.MockTransport(respond)).generate(evidence_payload("需先采购 SSO。"))
    assert len(calls) == 1 and error.value.provider_requests == 1


@pytest.mark.parametrize("kind", ["legacy_conditions", "empty_prerequisites", "duplicate_source"])
async def test_prerequisites_preserve_each_condition_and_selected_evidence(kind: str) -> None:
    payload = evidence_payload("需先采购和验收。")
    payload.evidence.extend(evidence_payload("当前未验收。").evidence)
    payload.evidence[1]["documentVersionId"] = str(payload.sources[0].version_id)

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        refs = [{"citationId": item["citationId"]} for item in sent["evidence"]]
        bound = [refs[0]]
        if kind == "duplicate_source":
            bound *= 2
        changes = {"conditions": ["需采购。"]} if kind == "legacy_conditions" else {}
        return model_response(
            [refs[0]],
            status="conditional",
            prerequisites=[
                {
                    "condition": "需验收。",
                    "state": "unmet",
                    "citations": bound,
                }
            ]
            if kind != "empty_prerequisites"
            else [],
            **changes,
        )

    with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$"):
        await gateway(httpx.MockTransport(respond)).generate(payload)


async def test_conditions_are_projected_once_from_ordered_outstanding_prerequisites() -> None:
    payload = evidence_payload("已采购。配置未完成。验收记录缺失。")

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        reference = {"citationId": sent["evidence"][0]["citationId"]}
        prerequisites = [
            {"condition": text, "state": state, "citations": [reference]}
            for text, state in [
                ("已采购模块。", "met"),
                ("需完成配置。", "unmet"),
                ("需确认验收结果。", "unknown"),
                ("需完成配置。", "unmet"),
            ]
        ]
        return model_response([], status="conditional", prerequisites=prerequisites)

    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert output.draft.conditions == ["需完成配置。", "需确认验收结果。"]
    assert len(output.draft.citations) == 1
    assert "prerequisites" not in output.model_dump_json()


@pytest.mark.parametrize("include_final_references", [True, False])
async def test_prerequisite_references_are_materialized_without_repeating_them(
    include_final_references: bool,
) -> None:
    payload = evidence_payload("签署协议后保证可用性 99.97%。")
    other = evidence_payload("本订单已签署上述协议。")
    payload.sources.extend(other.sources)
    payload.evidence.extend(other.evidence)

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        refs = [{"citationId": item["citationId"]} for item in sent["evidence"]]
        return model_response(
            refs[:1] if include_final_references else [],
            prerequisites=[{"condition": "需签署协议。", "state": "met", "citations": refs}],
        )

    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert output.draft.status == "supported"
    assert [c.excerpt for c in output.draft.citations] == [e["text"] for e in payload.evidence]


async def test_unknown_prerequisite_reference_is_rejected_without_repair() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return model_response(
            [],
            prerequisites=[
                {
                    "condition": "需签署协议。",
                    "state": "met",
                    "citations": [{"citationId": "foreign-request"}],
                }
            ],
        )

    with pytest.raises(PresalesError, match=r"^presales_invalid_citation$"):
        await gateway(httpx.MockTransport(respond)).generate(evidence_payload("已签署。"))


@pytest.mark.parametrize("field", ["answer", "prerequisite", "missingInformation"])
async def test_generated_business_prose_cannot_be_english_only(field: str) -> None:
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(sent)
        changes = {field: "Supported." if field == "answer" else ["Purchase SSO first."]}
        if field == "prerequisite":
            changes = {
                "prerequisites": [
                    {
                        "condition": "Purchase SSO first.",
                        "state": "unknown",
                        "citations": [{"citationId": sent["evidence"][0]["citationId"]}],
                    }
                ],
                "status": "conditional",
            }
        return model_response([{"citationId": sent["evidence"][0]["citationId"]}], **changes)

    with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$") as error:
        await gateway(httpx.MockTransport(respond)).generate(
            evidence_payload("Retention: 30 days.")
        )
    assert len(calls) == 1 and error.value.provider_requests == 1


@pytest.mark.parametrize("state,status", [("met", "supported"), ("unknown", "conditional")])
async def test_prerequisite_states_keep_chinese_prose_and_english_sources(state, status) -> None:
    original = "Enable SAML 2.0 after purchase and verification."

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        reference = {"citationId": sent["evidence"][0]["citationId"]}
        condition = "需采购 SAML 2.0 并验证。"
        return model_response(
            [reference],
            prerequisites=[{"condition": condition, "state": state, "citations": [reference]}],
            status=status,
            answer="已確認 SAML 2.0 的適用條件。",
        )

    output = await gateway(httpx.MockTransport(respond)).generate(evidence_payload(original))
    assert output.draft.status == status
    assert output.draft.citations[0].excerpt == original


async def test_model_must_explicitly_assess_prerequisites_even_when_none_apply() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        response = model_response([{"citationId": sent["evidence"][0]["citationId"]}])
        body = response.json()
        draft = json.loads(body["choices"][0]["message"]["content"])
        del draft["prerequisites"]
        body["choices"][0]["message"]["content"] = json.dumps(draft)
        return httpx.Response(200, json=body)

    with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$"):
        await gateway(httpx.MockTransport(respond)).generate(evidence_payload("无启用前提。"))


@pytest.mark.parametrize(
    "original",
    [
        "甲" * 1800,
        "背景说明。" * 90 + "\r\n可用性保证为 99.95%。" + "补充条件。" * 100,
        "Section one includes exceptions. " * 40,
        "  " + "保留 30 天\uff1b不含未采购服务。\n" * 70 + "  ",
    ],
    ids=["unbroken", "chinese_decimal_crlf", "english", "whitespace"],
)
async def test_long_retrieved_text_is_selectable_without_losing_or_rewriting_text(original) -> None:
    payload = evidence_payload(original)
    sent_evidence = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        sent_evidence.extend(sent["evidence"])
        assert all(0 < len(item["text"]) <= 600 for item in sent_evidence)
        return model_response([{"citationId": item["citationId"]} for item in sent_evidence])

    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    excerpts = [citation.excerpt for citation in output.draft.citations]
    assert len(excerpts) > 1
    assert "".join("".join(excerpts).split()) == "".join(original.split())
    position = 0
    for excerpt in excerpts:
        start = original.index(excerpt, position)
        assert not original[position:start].strip()
        position = start + len(excerpt)
    assert not original[position:].strip()
    assert len({item["citationId"] for item in sent_evidence}) == len(excerpts)


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "internal_uuid",
        "duplicate",
        "rewritten",
        "legacy",
        "same_version_conflict",
        "missing",
    ],
)
async def test_bad_selections_are_rejected_without_repair_or_retry(kind: str) -> None:
    payload = evidence_payload("月度可用性保证为 99.95%。")
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        reference = {"citationId": sent["evidence"][0]["citationId"]}
        calls.append(sent)
        if kind == "unknown":
            return model_response([{"citationId": "not-in-this-request"}])
        if kind == "internal_uuid":
            return model_response([{"citationId": payload.evidence[0]["chunkId"]}])
        if kind == "duplicate":
            return model_response([reference, reference])
        if kind == "rewritten":
            return model_response([{**reference, "excerpt": "月度可用性保证为 99.95%."}])
        if kind == "legacy":
            return model_response(
                [
                    {
                        key: value
                        for key, value in payload.evidence[0].items()
                        if key in {"chunkId", "documentVersionId"}
                    }
                    | {"excerpt": payload.evidence[0]["text"]}
                ]
            )
        if kind == "same_version_conflict":
            return model_response(
                [reference],
                status="conflicting_evidence",
                missingInformation=["请确认冲突条款的适用范围。"],
            )
        return model_response([])

    code = (
        "presales_invalid_citation"
        if kind in {"unknown", "internal_uuid", "duplicate"}
        else "presales_invalid_model_output"
    )
    with pytest.raises(PresalesError, match="^" + code + "$") as error:
        await gateway(httpx.MockTransport(respond)).generate(payload)
    assert len(calls) == 1 and error.value.provider_requests == 1


async def test_previous_request_references_cannot_be_reused() -> None:
    references = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        references.append(sent["evidence"][0]["citationId"])
        return model_response([{"citationId": references[0]}])

    client = gateway(httpx.MockTransport(respond))
    payload = evidence_payload("Same source text.")
    await client.generate(payload)
    with pytest.raises(PresalesError, match="presales_invalid_citation"):
        await client.generate(payload)
    assert len(references) == 2 and references[0] != references[1]


async def test_concurrent_requests_cannot_resolve_each_others_references() -> None:
    references = []
    both_entered = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        index = len(references)
        references.append(sent["evidence"][0]["citationId"])
        if len(references) == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 2)
        return model_response([{"citationId": references[1 - index]}])

    client = gateway(httpx.MockTransport(respond))
    results = await asyncio.gather(
        client.generate(evidence_payload("Tenant A evidence.")),
        client.generate(evidence_payload("Tenant B evidence.")),
        return_exceptions=True,
    )
    assert len(references) == 2 and references[0] != references[1]
    assert all(
        isinstance(result, PresalesError) and result.code == "presales_invalid_citation"
        for result in results
    )


@pytest.mark.parametrize("clarification", [[], ["请确认两份附件的优先级或适用范围。"]])
async def test_conflicting_selections_require_clarification_and_keep_both_versions(
    clarification: list[str],
) -> None:
    payload = evidence_payload("必须在境内保存。")
    other = evidence_payload("必须向境外复制。")
    payload.sources.extend(other.sources)
    payload.evidence.extend(other.evidence)

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        return model_response(
            [{"citationId": item["citationId"]} for item in reversed(sent["evidence"])],
            status="conflicting_evidence",
            missingInformation=clarification,
        )

    if not clarification:
        with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$") as error:
            await gateway(httpx.MockTransport(respond)).generate(payload)
        assert error.value.provider_requests == 1
        return
    output = await gateway(httpx.MockTransport(respond)).generate(payload)
    assert output.draft.status == "conflicting_evidence"
    assert [c.excerpt for c in output.draft.citations] == ["必须向境外复制。", "必须在境内保存。"]
    assert {c.document_version_id for c in output.draft.citations} == {
        s.version_id for s in payload.sources
    }


@pytest.mark.parametrize(
    "kind", ["foreign_version", "duplicate_candidate", "overlong_candidate", "too_many_candidates"]
)
async def test_invalid_evidence_is_rejected_before_model_dispatch(kind: str) -> None:
    payload = evidence_payload("Known source text.")
    if kind == "foreign_version":
        payload.evidence[0]["documentVersionId"] = str(uuid4())
    elif kind == "duplicate_candidate":
        payload.evidence.append(dict(payload.evidence[0]))
    elif kind == "overlong_candidate":
        payload.evidence[0]["text"] = "x" * 1801
    else:
        payload.evidence *= 13
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(PresalesError) as error:
        await gateway(httpx.MockTransport(respond)).generate(payload)
    assert error.value.code in {"presales_invalid_evidence", "presales_input_too_large"}
    assert error.value.provider_requests == 0 and not calls
