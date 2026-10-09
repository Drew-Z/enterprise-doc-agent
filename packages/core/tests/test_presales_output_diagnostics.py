import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput, SourceSnapshot


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("envelope", "envelope_json"),
        ("length", "incomplete_output"),
        ("tools", "unsafe_response"),
        ("json", "draft_json"),
        ("schema", "draft_schema"),
        ("quote", "basis_quote"),
        ("support", "support_quote"),
        ("combination", "support_combination"),
    ],
)
async def test_gateway_failure_categories_are_safe_and_keep_observed_usage(fault, expected):
    version = uuid4()
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="核实教育版权益。"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="public.txt",
                applicability="教育版采购要求",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
            )
        ],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(version),
                "text": "教师账号须提供100G空间。",
            }
        ],
    )
    calls = []

    def respond(request):
        calls.append(request)
        evidence = json.loads(json.loads(request.content)["messages"][1]["content"])["evidence"][0]
        quote = {"citationId": evidence["citationId"], "text": evidence["text"]}
        item = {
            "proposition": "教育版教师账号提供100G空间",
            "definition": [quote],
            "unconfirmed": [],
            "positive": [],
            "negative": [],
            "uncertainty": "missing",
        }
        draft = {
            "prerequisites": [item],
            "status": "insufficient_evidence",
            "answer": "缺少教育版的对应权益证明\uff0c暂不能确认。",
            "missingInformation": ["请提供本项目教育版权益表。"],
            "citations": [],
        }
        if fault == "schema":
            draft["provider_private_sentinel"] = "must-not-leak"
        if fault == "quote":
            item["definition"][0] = {**quote, "text": "provider_private_sentinel"}
        if fault == "support":
            item.update(
                uncertainty="none", negative=[{**quote, "text": "provider_private_sentinel"}]
            )
        if fault == "combination":
            item["positive"] = [quote]
        message = {"content": "provider_private_sentinel" if fault == "json" else json.dumps(draft)}
        if fault == "tools":
            message["tool_calls"] = [{"private": "provider_private_sentinel"}]
        response = {
            "id": "response-one",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "length" if fault == "length" else "stop",
                    "message": message,
                }
            ],
            "usage": {"total_tokens": 30},
        }
        if fault == "envelope":
            return httpx.Response(200, text="provider_private_sentinel")
        return httpx.Response(200, json=response, headers={"x-request-id": "request-one"})

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://test.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(payload)
    error = caught.value
    assert error.code == str(error) == "presales_invalid_model_output"
    assert error.diagnostic_code == expected
    assert error.provider_requests == len(calls) == 1 and not error.retryable
    assert "provider_private_sentinel" not in json.dumps(vars(error))
    if fault != "envelope":
        assert error.usage == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 30}
        assert error.provider_response_id == "response-one"
