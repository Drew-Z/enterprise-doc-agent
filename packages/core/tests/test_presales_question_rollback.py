import hashlib
import json

import httpx
import pytest
from packages.core.tests.test_presales_question_gateway import envelope, output, payload, settings
from pydantic import ValidationError

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.policy_gateway import freeze_policy, restore_gateway
from enterprise_doc_core.presales.question_assessment import question_assessment_system_message
from enterprise_doc_core.presales.settings import PresalesSettings


@pytest.mark.parametrize("version", ["presales.v21", "presales.v24"])
async def test_selected_prompt_survives_policy_restore_and_uses_exact_wire(version):
    config = PresalesSettings(primary_question_assessment=True, question_prompt_version=version)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=envelope(output(body)))

    gateway = OpenAICompatiblePresalesGateway(
        settings(), presales_settings=config, transport=httpx.MockTransport(respond)
    )
    expected = question_assessment_system_message(numeric_boundaries=version == "presales.v24")
    policy = freeze_policy(gateway, config, "auto", background=True)
    assert policy.routes[0].prompt_version == version
    assert policy.routes[0].prompt_sha256 == hashlib.sha256(expected.encode()).hexdigest()
    restored = restore_gateway(gateway, policy.routes[0])
    assert restored.provenance == gateway.provenance
    await restored.generate(payload())
    assert len(requests) == 1 and requests[0]["messages"][0]["content"] == expected
    other = "presales.v24" if version == "presales.v21" else "presales.v21"
    changed = OpenAICompatiblePresalesGateway(
        settings(), presales_settings=config.model_copy(update={"question_prompt_version": other})
    )
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(changed, policy.routes[0])


async def test_rollback_prompt_retains_original_projection_rejection_without_retry():
    calls = []

    def respond(request):
        value = output(json.loads(request.content))
        value["responses"][0]["missingInformation"] = [f"待确认第{i}项资料。" for i in range(12)]
        calls.append(value)
        return httpx.Response(200, json=envelope(value))

    gateway = OpenAICompatiblePresalesGateway(
        settings(),
        presales_settings=PresalesSettings(
            primary_question_assessment=True, question_prompt_version="presales.v21"
        ),
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(PresalesError, match="presales_invalid_model_output"):
        await gateway.generate(payload())
    assert len(calls) == 1


@pytest.mark.parametrize("version", ["presales.v22", "presales.v23", "unknown", ""])
def test_only_two_reviewed_runtime_prompt_versions_are_allowed(version):
    with pytest.raises(ValidationError):
        PresalesSettings(question_prompt_version=version)
