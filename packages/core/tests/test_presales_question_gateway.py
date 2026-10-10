import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.policy import ExecutionPolicy
from enterprise_doc_core.presales.policy_gateway import freeze_policy, restore_gateway
from enterprise_doc_core.presales.question_assessment import question_assessment_system_message
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput, SourceSnapshot
from enterprise_doc_core.presales.settings import PresalesSettings


def settings():
    return ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://primary.invalid/v1",
        api_key=SecretStr("test-only"),
        model_name="gpt-6-luna",
        reasoning_effort="low",
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://fallback.invalid/v1",
        fallback_api_key=SecretStr("test-fallback"),
        fallback_model_name="grok-4.7",
    )


def payload(key="Q1"):
    version = uuid4()
    return GenerationInput(
        requirement=RequirementInput(key=key, text="说明本订单能否启用。说明校验状态与下一步。"),
        sources=[
            SourceSnapshot(
                version_id=version,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="订单资料.txt",
                version_number=1,
                latest_version_number=1,
                content_sha256="a" * 64,
                applicability="仅本订单",
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


def output(body):
    wire = json.loads(body["messages"][1]["content"])
    spans = {s["text"]: {"spanId": s["spanId"]} for s in wire["spans"]}
    return {
        "rules": [{"proposition": "通过校验", "requiredBy": [spans["启用前必须通过校验。"]]}],
        "assessments": [
            {
                "ruleIndex": 0,
                "state": "unknown",
                "uncertainty": "missing",
                "observations": [spans["校验结果未登记。"]],
                "summary": "没有已通过或未通过的状态记录。",
                "nextAction": "确认校验实际结果。",
            }
        ],
        "responses": [
            {
                "requirementPartId": p["requirementPartId"],
                "answer": "尚不能确认本订单校验通过\uff0c须核实实际结果。",
                "citations": [{"citationId": wire["evidence"][0]["citationId"]}],
                "missingInformation": ["实际校验结果是什么\uff1f"],
            }
            for p in wire["requirementParts"]
        ],
        "status": "conditional",
        "conclusion": "当前不能确认全部启用条件已满足。",
    }


def envelope(value):
    return {
        "id": "test-question-response",
        "model": "gpt-6-luna",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"content": json.dumps(value)}}
        ],
        "usage": {"total_tokens": 42},
    }


@pytest.mark.parametrize("streaming", [False, True])
async def test_explicit_question_protocol_uses_existing_schema_and_projects_original_draft(
    streaming,
):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages"][0]["content"] == question_assessment_system_message()
        assert body["max_completion_tokens"] == 4000
        assert "max_tokens" not in body and "tools" not in body and "tool_choice" not in body
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        value = output(body)
        Draft202012Validator(schema["schema"]).validate(value)
        if streaming:
            chunk = envelope(value)
            chunk["choices"][0]["delta"] = chunk["choices"][0].pop("message")
            return httpx.Response(
                200,
                text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=envelope(value))

    gateway = OpenAICompatiblePresalesGateway(
        settings().model_copy(update={"streaming": streaming}),
        presales_settings=PresalesSettings(primary_question_assessment=True),
        transport=httpx.MockTransport(respond),
    )
    result = await gateway.generate(payload())
    assert gateway.provenance["promptVersion"] == "presales.v21"
    assert result.returned_model == "gpt-6-luna" and result.usage["total_tokens"] == 42
    assert len(requests) == 1 and len(result.draft.prerequisites) == 1
    assert result.draft.prerequisites[0].state == "unknown"
    assert "说明校验状态与下一步。" in result.draft.answer
    assert "确认校验实际结果。" in result.draft.missing_information
    assert result.draft.citations[0].excerpt == "启用前必须通过校验。校验结果未登记。"


@pytest.mark.parametrize(
    "fault", ["foreign_question", "omitted_question", "foreign_span", "503", "502"]
)
async def test_question_output_rejects_invalid_selection_and_never_repairs_or_retries(fault):
    calls = []

    def respond(request):
        calls.append(True)
        if fault in {"502", "503"}:
            return httpx.Response(int(fault))
        value = output(json.loads(request.content))
        if fault == "foreign_question":
            value["responses"][0]["requirementPartId"] = "foreign"
        elif fault == "omitted_question":
            value["responses"].pop()
        else:
            value["rules"][0]["requiredBy"] = [{"spanId": "foreign"}]
        return httpx.Response(200, json=envelope(value))

    gateway = OpenAICompatiblePresalesGateway(
        settings(), question_assessment=True, transport=httpx.MockTransport(respond)
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(payload())
    assert len(calls) == caught.value.provider_requests == 1
    assert caught.value.retryable is (fault in {"502", "503"})


async def test_overlapping_requests_resolve_only_their_own_question_and_source():
    count = 0
    both = asyncio.Event()

    async def respond(request):
        nonlocal count
        body = json.loads(request.content)
        count += 1
        if count == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        return httpx.Response(200, json=envelope(output(body)))

    gateway = OpenAICompatiblePresalesGateway(
        settings(), question_assessment=True, transport=httpx.MockTransport(respond)
    )
    first, second = payload("Q1"), payload("Q2")
    a, b = await asyncio.gather(gateway.generate(first), gateway.generate(second))
    assert a.draft.citations[0].document_version_id == first.sources[0].version_id
    assert b.draft.citations[0].document_version_id == second.sources[0].version_id
    assert count == 2


def test_question_mode_is_independent_frozen_and_restored_with_drift_rejection():
    config = PresalesSettings(
        primary_question_assessment=True,
        background_generation_enabled=True,
        automatic_failover_enabled=True,
    )
    primary = OpenAICompatiblePresalesGateway(settings(), presales_settings=config)
    frozen = ExecutionPolicy.model_validate_json(
        freeze_policy(primary, config, "auto", background=True).model_dump_json()
    )
    assert [r.prompt_version for r in frozen.routes] == ["presales.v21", "presales.v15"]
    restored = restore_gateway(primary, frozen.routes[0])
    assert restored.question_assessment and restored.provenance == primary.provenance
    for legacy in (
        OpenAICompatiblePresalesGateway(settings()),
        OpenAICompatiblePresalesGateway(settings(), strict_output=True),
    ):
        with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
            restore_gateway(legacy, frozen.routes[0])
    fallback = OpenAICompatiblePresalesGateway(
        settings(),
        presales_settings=config.model_copy(
            update={"model_route": "fallback", "fallback_question_assessment": True}
        ),
    )
    assert fallback.question_assessment and fallback.model_name == "grok-4.7"
