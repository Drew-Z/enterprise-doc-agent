from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput, SourceSnapshot


async def generate(
    *, change=None, record="安全培训状态未更新。", obligation="操作人员必须完成安全培训。"
):
    versions = [uuid4(), uuid4()]
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="能否开始操作\uff1f"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                applicability="本订单",
                filename=f"source-{index}.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
            )
            for index, version in enumerate(versions)
        ],
        evidence=[
            {"chunkId": str(uuid4()), "documentVersionId": str(version), "text": text}
            for version, text in zip(versions, [obligation, record], strict=True)
        ],
    )
    calls = []

    async def respond(request):
        envelope = json.loads(request.content)
        calls.append(envelope)
        offered = json.loads(envelope["messages"][1]["content"])["evidence"]
        quotes = [{"citationId": e["citationId"], "text": e["text"]} for e in offered]
        draft = {
            "status": "conditional",
            "answer": "需确认安全培训是否完成。",
            "missingInformation": ["请确认安全培训是否完成并提供依据。"],
            "prerequisites": [
                {
                    "proposition": "操作人员已完成安全培训。",
                    "definition": quotes[:1],
                    "unconfirmed": quotes[1:],
                    "positive": [],
                    "negative": [],
                    "uncertainty": "missing",
                }
            ],
        }
        if change:
            change(draft, quotes)
        return httpx.Response(
            200,
            headers={"x-oneapi-request-id": "basis-request"},
            json={
                "id": "basis-response",
                "model": "controlled",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"content": json.dumps(draft)}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://basis.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        transport=httpx.MockTransport(respond),
    )
    try:
        return await gateway.generate(payload)
    finally:
        assert len(calls) == 1


async def test_definition_and_unconfirmed_status_survive_public_projection():
    result = await generate()
    assert result.draft.prerequisites[0].state == "unknown"
    assert result.draft.prerequisites[0].citation_indexes == [0, 1]
    assert [c.excerpt for c in result.draft.citations] == [
        "操作人员必须完成安全培训。",
        "安全培训状态未更新。",
    ]
    assert result.draft.answer == "需确认安全培训是否完成。"
    assert result.provider_request_id == "basis-request"
    assert result.provider_response_id == "basis-response"
    assert result.usage["total_tokens"] == 30
    assert "definition" not in result.draft.model_dump()


@pytest.mark.parametrize("field", ["definition", "unconfirmed"])
@pytest.mark.parametrize("fault", ["foreign_reference", "invented_quote", "wrong_fragment"])
async def test_each_basis_quote_is_bound_to_its_own_offered_fragment(field, fault):
    def change(draft, quotes):
        quote = draft["prerequisites"][0][field][0]
        if fault == "foreign_reference":
            quote["citationId"] = "cite_previous_request_1"
        elif fault == "invented_quote":
            quote["text"] = "培训明确未完成。"
        else:
            quote["citationId"] = quotes[1 if field == "definition" else 0]["citationId"]

    with pytest.raises(PresalesError) as caught:
        await generate(change=change)
    assert caught.value.code == "presales_invalid_model_output"
    assert caught.value.provider_requests == 1
    assert caught.value.provider_request_id == "basis-request"
    assert caught.value.provider_response_id == "basis-response"
    assert caught.value.usage["total_tokens"] == 30


@pytest.mark.parametrize(
    "fault",
    [
        "missing_definition",
        "missing_question",
        "gap_used_as_denial",
        "claimed_supported",
        "legacy_citations",
        "explicit_state",
        "english_proposition",
    ],
)
async def test_incomplete_or_inconsistent_basis_is_rejected(fault):
    def change(draft, quotes):
        item = draft["prerequisites"][0]
        if fault == "missing_definition":
            item["definition"] = []
        elif fault == "missing_question":
            draft["missingInformation"] = []
        elif fault == "gap_used_as_denial":
            item.update(uncertainty="none", negative=quotes[1:])
        elif fault == "claimed_supported":
            draft["status"] = "supported"
        elif fault == "legacy_citations":
            item["citations"] = [{"citationId": quotes[0]["citationId"]}]
        elif fault == "explicit_state":
            item["state"] = "met"
        else:
            item["proposition"] = "Personnel completed safety training."

    with pytest.raises(PresalesError, match=r"^presales_invalid_model_output$"):
        await generate(change=change)


@pytest.mark.parametrize("state,record", [("met", "培训已完成。"), ("unmet", "培训尚未完成。")])
async def test_explicit_event_state_keeps_its_direction(state, record):
    def change(draft, quotes):
        item = draft["prerequisites"][0]
        item.update(unconfirmed=[], uncertainty="none")
        item["positive" if state == "met" else "negative"] = quotes[1:]
        draft.update(
            status="supported" if state == "met" else "conditional",
            missingInformation=[],
            answer="已完成培训。" if state == "met" else "需完成培训。",
        )

    result = await generate(change=change, record=record)
    assert result.draft.prerequisites[0].state == state
    assert result.draft.prerequisites[0].citation_indexes == [0, 1]


async def test_actual_record_submission_obligation_can_be_unmet():
    def change(draft, quotes):
        draft["prerequisites"][0].update(
            proposition="培训证书已提交。",
            unconfirmed=[],
            negative=[{**quotes[1], "text": "证书尚未提交。"}],
            uncertainty="none",
        )
        draft.update(answer="培训已完成\uff0c仍需提交证书。", missingInformation=[])

    result = await generate(
        change=change,
        record="培训已完成\uff0c证书尚未提交。",
        obligation="开工前必须提交培训证书。",
    )
    assert result.draft.prerequisites[0].state == "unmet"


async def test_two_definition_quotes_from_one_fragment_preserve_one_reference():
    def change(draft, quotes):
        draft["prerequisites"][0]["definition"] = [
            {**quotes[0], "text": text}
            for text in ["操作人员必须完成安全培训。", "培训通过后可以操作。"]
        ]

    result = await generate(
        change=change, obligation="操作人员必须完成安全培训。培训通过后可以操作。"
    )
    assert len(result.draft.citations) == 2
    assert result.draft.prerequisites[0].citation_indexes == [0, 1]
