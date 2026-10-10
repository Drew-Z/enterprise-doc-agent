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


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("question", "requirement_coverage"),
        ("rule", "rule_coverage"),
        ("answer", "answer_evidence"),
        ("language", "generated_language"),
        ("projection", "draft_projection"),
        ("status", "status_consistency"),
        ("span", "basis_quote"),
        ("valid", None),
    ],
)
async def test_question_contract_failures_are_distinct_without_exposing_content(fault, expected):
    version = uuid4()
    payload = GenerationInput(
        requirement=RequirementInput(key="Q1", text="请核验安全培训要求。请说明记录。"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="public-policy.txt",
                applicability="本次培训范围",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
            )
        ],
        evidence=[
            {
                "chunkId": str(uuid4()),
                "documentVersionId": str(version),
                "text": "员工必须完成安全培训。",
            }
        ],
    )
    calls = []

    def respond(request):
        calls.append(request)
        offered = json.loads(json.loads(request.content)["messages"][1]["content"])
        draft = {
            "rules": [],
            "assessments": [],
            "responses": [
                {
                    "requirementPartId": part["requirementPartId"],
                    "answer": "未见本项目的培训完成记录。",
                    "citations": [],
                    "missingInformation": ["请提供对应培训记录。"],
                }
                for part in offered["requirementParts"]
            ],
            "status": "insufficient_evidence",
            "conclusion": "尚不能确认全部要求。",
        }
        if fault == "question":
            draft["responses"][1]["requirementPartId"] = draft["responses"][0]["requirementPartId"]
        elif fault in {"rule", "span"}:
            draft["rules"] = [
                {
                    "proposition": "员工完成安全培训",
                    "requiredBy": [{"spanId": offered["spans"][0]["spanId"]}],
                }
            ]
            if fault == "span":
                draft["rules"][0]["requiredBy"][0]["spanId"] = "provider_private_sentinel"
                draft["assessments"] = [
                    {
                        "ruleIndex": 0,
                        "state": "unknown",
                        "uncertainty": "missing",
                        "summary": "未见完成记录。",
                        "observations": [],
                        "nextAction": "请确认是否已完成培训。",
                    }
                ]
        elif fault == "answer":
            draft["responses"][0]["missingInformation"] = []
        elif fault == "language":
            draft["responses"][0]["answer"] = "provider_private_sentinel"
        elif fault == "projection":
            for response in draft["responses"]:
                response["answer"] = "中" * 1975 + "provider_private_sentinel"
        elif fault == "status":
            draft["status"] = "conditional"
        return httpx.Response(
            200,
            json={
                "id": "question-response",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}],
                "usage": {"total_tokens": 31},
            },
            headers={"x-request-id": "question-request"},
        )

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://test.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        question_assessment=True,
        transport=httpx.MockTransport(respond),
    )
    if expected is None:
        result = await gateway.generate(payload)
        assert result.draft.status == "insufficient_evidence"
        assert len(calls) == 1
        return
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(payload)
    error = caught.value
    assert error.code == str(error) == "presales_invalid_model_output"
    assert error.diagnostic_code == expected
    assert error.provider_requests == len(calls) == 1 and not error.retryable
    assert error.usage == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 31}
    assert error.provider_response_id == "question-response"
    assert error.provider_request_id == "question-request"
    assert "provider_private_sentinel" not in json.dumps(vars(error))
