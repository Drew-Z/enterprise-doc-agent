from __future__ import annotations

import asyncio
import json
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from enterprise_doc_core.agents import (
    AgentRunTaskType,
    BehaviorVersions,
    DeterministicGroundedGateway,
    GroundedEvidence,
    GroundedModelRequest,
    ModelAuthError,
    ModelContractError,
    ModelOutputSchemaError,
    ModelRateLimitedError,
    ModelResponseTooLarge,
    ModelServerError,
    ModelTimeoutError,
    OpenAICompatibleChatGateway,
    StructuredExtractionSchema,
    gateway_error_is_retryable,
)
from enterprise_doc_core.config import ModelSettings

TENANT = UUID("00000000-0000-0000-0000-000000000001")
VERSION = UUID("00000000-0000-0000-0000-000000000011")
GENERATION = UUID("00000000-0000-0000-0000-000000000021")


def _evidence(*, chunk_number: int = 1, score: float = 0.9) -> GroundedEvidence:
    return GroundedEvidence(
        chunk_id=UUID(f"00000000-0000-0000-0000-{chunk_number:012d}"),
        tenant_id=TENANT,
        document_version_id=VERSION,
        generation_id=GENERATION,
        text=f"Payment is due within {chunk_number * 30} days.",
        rank=chunk_number,
        score=score,
        page_number=chunk_number,
        heading="Payment",
        source_filename="contract.txt",
        start_offset=chunk_number * 10,
        end_offset=chunk_number * 10 + 30,
    )


def _request(
    *,
    task_type: AgentRunTaskType = AgentRunTaskType.QUESTION_ANSWER,
    evidence: list[GroundedEvidence] | None = None,
    extraction_schema: StructuredExtractionSchema | None = None,
) -> GroundedModelRequest:
    return GroundedModelRequest(
        task_type=task_type,
        user_input="What are the payment terms?",
        evidence=evidence if evidence is not None else [_evidence()],
        extraction_schema=extraction_schema,
        behavior_versions=BehaviorVersions(
            graph_version="m4.v1",
            prompt_version="m4.v1",
            tool_schema_version="m4.v1",
        ),
    )


def _settings(**overrides: object) -> ModelSettings:
    values: dict[str, object] = {
        "provider": "openai_compatible",
        "base_url": "https://model.example.test/v1",
        "api_key": SecretStr("test-secret-key"),
        "model_name": "test-model",
        "model_version": "2026-07",
        "timeout_seconds": 2.0,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return ModelSettings(**values)


def _completion(content: str, *, model: str = "served-model") -> dict[str, object]:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }


def _valid_payload(request: GroundedModelRequest) -> dict[str, object]:
    evidence = request.evidence[0]
    return {
        "task_type": request.task_type.value,
        "answer_text": "Payment is due within 30 days.",
        "structured_fields": None,
        "citations": [
            {
                "chunk_id": str(evidence.chunk_id),
                "document_version_id": str(evidence.document_version_id),
                "excerpt": "Payment is due within 30 days.",
            }
        ],
        "risk_hint": "low",
    }


async def test_deterministic_gateway_is_stable_for_all_task_types() -> None:
    gateway = DeterministicGroundedGateway()
    qa = await gateway.generate(_request())
    summary = await gateway.generate(_request(task_type=AgentRunTaskType.SUMMARY))
    schema = StructuredExtractionSchema.model_validate(
        {
            "type": "object",
            "properties": {
                "payment_term": {"type": "string", "minLength": 1},
                "days": {"type": "integer", "minimum": 1},
            },
            "required": ["payment_term", "days"],
        }
    )
    extraction_request = _request(
        task_type=AgentRunTaskType.STRUCTURED_EXTRACTION,
        extraction_schema=schema,
    )
    extraction = await gateway.generate(extraction_request)
    extraction_replay = await gateway.generate(extraction_request)

    assert qa.payload.task_type is AgentRunTaskType.QUESTION_ANSWER
    assert summary.payload.task_type is AgentRunTaskType.SUMMARY
    assert extraction.payload.structured_fields == extraction_replay.payload.structured_fields
    assert qa.payload.citations[0].chunk_id == extraction.payload.citations[0].chunk_id
    assert qa.identity.provider == "deterministic"


async def test_deterministic_gateway_rejects_empty_evidence() -> None:
    gateway = DeterministicGroundedGateway()
    with pytest.raises(ModelContractError):
        await gateway.generate(_request(evidence=[]))


async def test_openai_gateway_sends_strict_json_request_without_secret_or_tools() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completion(json.dumps(_valid_payload(_request()))))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        output = await gateway.generate(_request())
    finally:
        await client.aclose()

    assert output.identity.provider == "openai_compatible"
    assert output.identity.model_name == "served-model"
    assert output.repaired is False
    assert len(requests) == 1
    sent = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-secret-key"
    assert sent["temperature"] == 0
    assert sent["response_format"] == {"type": "json_object"}
    assert "tools" not in sent
    assert "test-secret-key" not in requests[0].content.decode()
    assert "tenant_id" not in sent["messages"][1]["content"]


async def test_openai_gateway_performs_at_most_one_bounded_schema_repair() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else json.dumps(_valid_payload(_request()))
        return httpx.Response(200, json=_completion(content))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        output = await gateway.generate(_request())
    finally:
        await client.aclose()

    assert calls == 2
    assert output.repaired is True


async def test_schema_repair_receives_exact_contract_and_safe_validation_issues() -> None:
    requests: list[httpx.Request] = []
    invalid_payload = _valid_payload(_request())
    invalid_payload["structured_fields"] = {"payment_term": "30 days"}
    invalid_payload["risk_hint"] = "No customer data"
    citation = invalid_payload["citations"][0]
    assert isinstance(citation, dict)
    citation.pop("excerpt")

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = (
            json.dumps(invalid_payload)
            if len(requests) == 1
            else json.dumps(_valid_payload(_request()))
        )
        return httpx.Response(200, json=_completion(content))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        output = await gateway.generate(_request())
    finally:
        await client.aclose()

    assert output.repaired is True
    assert len(requests) == 2
    initial_messages = json.loads(requests[0].content)["messages"]
    assert "structured_fields must be JSON null" in initial_messages[0]["content"]
    assert (
        'risk_hint must be JSON null or exactly one of "low", "medium", or "high"'
        in (initial_messages[0]["content"])
    )
    repair_messages = json.loads(requests[1].content)["messages"]
    repair_instruction = repair_messages[-1]["content"]
    assert "question_answer.citations.0.excerpt:missing" in repair_instruction
    assert "question_answer.risk_hint:enum" in repair_instruction
    assert "question_answer.structured_fields:none_required" in repair_instruction
    assert "No customer data" not in repair_instruction


async def test_schema_repair_shares_one_total_timeout_budget() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=_completion("not-json"))
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=_completion(json.dumps(_valid_payload(_request()))))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(
        settings=_settings(timeout_seconds=0.05),
        client=client,
    )
    try:
        with pytest.raises(ModelTimeoutError):
            await gateway.generate(_request())
    finally:
        await client.aclose()

    assert calls == 2


async def test_openai_gateway_schema_repair_failure_is_permanent() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("still-invalid"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        with pytest.raises(ModelOutputSchemaError) as error:
            await gateway.generate(_request())
    finally:
        await client.aclose()
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("status_code", "error_type", "retryable"),
    [
        (401, ModelAuthError, False),
        (400, ModelContractError, False),
        (429, ModelRateLimitedError, True),
        (500, ModelServerError, True),
        (503, ModelServerError, True),
    ],
)
async def test_openai_gateway_classifies_provider_failures(
    status_code: int,
    error_type: type[Exception],
    retryable: bool,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "provider failure"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        with pytest.raises(error_type) as error:
            await gateway.generate(_request())
    finally:
        await client.aclose()
    assert gateway_error_is_retryable(error.value) is retryable


async def test_openai_gateway_classifies_timeout_and_response_size() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    timeout_client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler),
        trust_env=False,
    )
    timeout_gateway = OpenAICompatibleChatGateway(settings=_settings(), client=timeout_client)
    try:
        with pytest.raises(ModelTimeoutError):
            await timeout_gateway.generate(_request())
    finally:
        await timeout_client.aclose()

    async def large_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    large_client = httpx.AsyncClient(
        transport=httpx.MockTransport(large_handler),
        trust_env=False,
    )
    large_gateway = OpenAICompatibleChatGateway(settings=_settings(), client=large_client)
    try:
        with pytest.raises(ModelResponseTooLarge):
            await large_gateway.generate(_request())
    finally:
        await large_client.aclose()


def test_supported_extraction_schema_rejects_open_ended_or_unknown_keywords() -> None:
    with pytest.raises(ValidationError):
        StructuredExtractionSchema.model_validate(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        )
    with pytest.raises(ValidationError):
        StructuredExtractionSchema.model_validate(
            {"type": "object", "properties": {}, "$ref": "https://example.test/schema"}
        )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "enum": [True]},
        {"type": "integer", "enum": ["1"]},
        {"type": "boolean", "enum": [1]},
        {"type": "number", "enum": [float("nan")]},
        {"type": "integer", "minimum": 1.5, "maximum": 1.6},
    ],
)
def test_extraction_schema_rejects_incompatible_enum_and_numeric_bounds(
    schema: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        StructuredExtractionSchema.model_validate(
            {"type": "object", "properties": {"value": schema}}
        )


@pytest.mark.parametrize("status_code", [408, 425])
async def test_openai_gateway_classifies_provider_timeout_status_as_retryable(
    status_code: int,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "provider timeout"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    gateway = OpenAICompatibleChatGateway(settings=_settings(), client=client)
    try:
        with pytest.raises(ModelTimeoutError) as error:
            await gateway.generate(_request())
    finally:
        await client.aclose()
    assert gateway_error_is_retryable(error.value) is True
