import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.policy import ExecutionPolicy
from enterprise_doc_core.presales.policy_gateway import freeze_policy, restore_gateway
from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput
from enterprise_doc_core.presales.settings import PresalesSettings


def settings():
    return ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://primary.invalid/v1",
        api_key=SecretStr("test-only"),
        model_name="controlled",
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://fallback.invalid/v1",
        fallback_api_key=SecretStr("test-only-fallback"),
        fallback_model_name="controlled-fallback",
    )


def payload():
    return GenerationInput(
        requirement=RequirementInput(key="Q1", text="说明存储安全措施。"), sources=[], evidence=[]
    )


def draft():
    return {
        "status": "insufficient_evidence",
        "answer": "未提供实现资料。",
        "missingInformation": ["请补充加密配置与密钥管理说明。"],
        "citations": [],
        "prerequisites": [],
    }


def envelope(value):
    return {
        "id": "test-response",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
        "usage": {"total_tokens": 20},
    }


@pytest.mark.parametrize("strict", [False, True])
async def test_wire_contract_and_valid_draft_keep_legacy_projection(strict):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if strict:
            output = body["response_format"]
            assert output["type"] == "json_schema" and output["json_schema"]["strict"] is True
            schema = output["json_schema"]["schema"]
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(draft())
            assert schema["properties"]["missingInformation"]["type"] == "array"
            assert len(schema["properties"]["prerequisites"]["items"]["anyOf"]) == 4
            assert set(schema["$defs"]["SpanReference"]["properties"]) == {"spanId"}
            assert json.loads(body["messages"][1]["content"])["spans"] == []
            assert "每条都包含本次citationId及逐字连续text" not in body["messages"][0]["content"]
            for obj in [schema, *schema["$defs"].values()]:
                if obj.get("type") == "object":
                    assert obj["additionalProperties"] is False
                    assert set(obj["required"]) == set(obj["properties"])
            assert body["messages"][0]["content"].endswith(json.dumps(schema, ensure_ascii=False))
        else:
            assert body["response_format"] == {"type": "json_object"}
        assert body["tools"] == [] and body["tool_choice"] == "none"
        return httpx.Response(200, json=envelope(draft()))

    gateway = OpenAICompatiblePresalesGateway(
        settings(), strict_output=strict, transport=httpx.MockTransport(respond)
    )
    result = await gateway.generate(payload())
    assert result.draft.answer == draft()["answer"]
    assert result.draft.prerequisites == result.draft.conditions == []
    assert result.usage["total_tokens"] == 20 and len(requests) == 1
    assert gateway.provenance["promptVersion"] == ("presales.v20" if strict else "presales.v15")
    if not strict:
        assert gateway.provenance["promptSha256"] == (
            "318fc29ef2903cef5ad51a59163fad35ff855aca012bbae986e84a0fbb83d2ab"
        )


@pytest.mark.parametrize("fault", ["string_array", "missing_array", "foreign_citation", "length"])
async def test_strict_mode_still_rejects_invalid_provider_output_without_repair(fault):
    value = draft()
    if fault == "string_array":
        value["missingInformation"] = "请补充实现材料。"
    elif fault == "missing_array":
        del value["citations"]
    elif fault == "foreign_citation":
        value["citations"] = [{"citationId": "cite_not_offered_1"}]
    else:
        value["answer"] = "待" * 4001
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=envelope(value))

    gateway = OpenAICompatiblePresalesGateway(
        settings(), strict_output=True, transport=httpx.MockTransport(respond)
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(payload())
    assert len(requests) == caught.value.provider_requests == 1
    assert caught.value.diagnostic_code == (
        "citation" if fault == "foreign_citation" else "draft_schema"
    )
    assert not caught.value.retryable and caught.value.usage["total_tokens"] == 20


async def test_unsupported_strict_mode_does_not_silently_downgrade():
    requests = []

    def reject(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "private-provider-details"}})

    gateway = OpenAICompatiblePresalesGateway(
        settings(), strict_output=True, transport=httpx.MockTransport(reject)
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(payload())
    assert len(requests) == 1 and requests[0]["response_format"]["type"] == "json_schema"
    assert caught.value.code == "presales_model_failed" and not caught.value.retryable
    assert "private-provider-details" not in json.dumps(vars(caught.value))


def test_selected_route_mode_is_frozen_and_restored_without_changing_other_route():
    config = PresalesSettings(
        background_generation_enabled=True,
        automatic_failover_enabled=True,
        primary_strict_output=True,
    )
    primary = OpenAICompatiblePresalesGateway(settings(), presales_settings=config)
    frozen = freeze_policy(primary, config, "auto", background=True)
    restored_policy = ExecutionPolicy.model_validate_json(frozen.model_dump_json(by_alias=True))
    assert [r.prompt_version for r in restored_policy.routes] == ["presales.v20", "presales.v15"]
    restored = restore_gateway(primary, restored_policy.routes[0])
    assert restored.strict_output is True and restored.provenance == primary.provenance
    historical_v18 = restored_policy.routes[0].model_copy(
        update={
            "prompt_version": "presales.v18",
            "prompt_sha256": "6c65e4674e84d8a6a5639a08905ef270e48dce3a27a474927a0c7038995b4a29",
        }
    )
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(primary, historical_v18)
    historical_v19 = restored_policy.routes[0].model_copy(
        update={
            "prompt_version": "presales.v19",
            "prompt_sha256": "655d974ce22b099f135110c5be5fe0c766132960e48e39b41f38760095ea2249",
        }
    )
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(primary, historical_v19)
    legacy = OpenAICompatiblePresalesGateway(settings())
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(legacy, restored_policy.routes[0])
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(
            primary, freeze_policy(legacy, PresalesSettings(), "auto", background=True).routes[0]
        )
    fallback = OpenAICompatiblePresalesGateway(
        settings(), presales_settings=config.model_copy(update={"model_route": "fallback"})
    )
    assert restore_gateway(fallback, restored_policy.routes[1]).strict_output is False


def test_fallback_can_explicitly_select_strict_mode_independently():
    config = PresalesSettings(model_route="fallback", fallback_strict_output=True)
    gateway = OpenAICompatiblePresalesGateway(settings(), presales_settings=config)
    assert gateway.strict_output is True and gateway.model_name == "controlled-fallback"
