from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.export import safe_cell
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.generation import resolve_draft
from enterprise_doc_core.presales.schemas import (
    CreatePacket,
    GenerationInput,
    ModelDraft,
    RequirementInput,
    SourceSnapshot,
)
from enterprise_doc_core.presales.settings import PresalesSettings


def draft_payload() -> dict:
    return {
        "status": "insufficient_evidence",
        "answer": "现有片段不能证明该能力。",
        "missingInformation": ["请补充有效能力证明。"],
        "citations": [],
        "prerequisites": [],
    }


@pytest.mark.parametrize(
    "status,conditions,missing,citations",
    [
        ("supported", [], [], []),
        ("conditional", [], [], []),
        ("insufficient_evidence", [], [], []),
        ("conflicting_evidence", [], [], []),
    ],
)
def test_incomplete_commitments_are_rejected(status, conditions, missing, citations) -> None:
    with pytest.raises(ValidationError):
        ModelDraft.model_validate(
            {
                "status": status,
                "answer": "Answer",
                "conditions": conditions,
                "missingInformation": missing,
                "citations": citations,
            }
        )


def test_duplicate_ids_and_excessive_scope_are_rejected() -> None:
    source = {"versionId": str(uuid4()), "applicability": "采购项目"}
    requirement = {"key": "R1", "text": "Must retain logs"}
    for sources, requirements in [
        ([source, source], [requirement]),
        ([source], [requirement, requirement]),
        ([source] * 7, [requirement]),
    ]:
        with pytest.raises(ValidationError):
            CreatePacket.model_validate(
                {"title": "采购", "sources": sources, "requirements": requirements}
            )


@pytest.mark.parametrize("indexes", [[], [True], [-1], [1], [12], [0, 0], ["0"]])
def test_prerequisites_require_distinct_bound_integer_citation_indexes(indexes) -> None:
    with pytest.raises(ValidationError):
        ModelDraft.model_validate(
            {
                "status": "conditional",
                "answer": "需确认验收。",
                "conditions": ["需确认验收。"],
                "prerequisites": [
                    {"condition": "需确认验收。", "state": "unknown", "citationIndexes": indexes}
                ],
                "citations": [
                    {
                        "chunkId": str(uuid4()),
                        "documentVersionId": str(uuid4()),
                        "excerpt": "验收未登记。",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "status,conditions", [("supported", []), ("conditional", ["改写的条件。"]), ("conditional", [])]
)
def test_structured_prerequisites_cannot_be_hidden_by_overall_assessment(
    status, conditions
) -> None:
    with pytest.raises(ValidationError):
        ModelDraft.model_validate(
            {
                "status": status,
                "answer": "核对验收。",
                "conditions": conditions,
                "prerequisites": [
                    {"condition": "需确认验收。", "state": "unknown", "citationIndexes": [0]}
                ],
                "citations": [
                    {
                        "chunkId": str(uuid4()),
                        "documentVersionId": str(uuid4()),
                        "excerpt": "验收未登记。",
                    }
                ],
            }
        )


def test_citations_are_bound_to_tenant_version_candidate_and_exact_excerpt() -> None:
    tenant, version, generation, chunk = (uuid4() for _ in range(4))
    source = SourceSnapshot(
        version_id=version,
        applicability="项目范围",
        document_id=uuid4(),
        generation_id=generation,
        filename="policy.txt",
        version_number=1,
        latest_version_number=1,
        content_sha256="a" * 64,
    )
    candidate = RetrievalCandidate(
        chunk_id=chunk,
        tenant_id=tenant,
        document_version_id=version,
        generation_id=generation,
        text="Retention is 30 days.",
        source_filename="policy.txt",
        end_offset=21,
    )
    draft = ModelDraft.model_validate(
        {
            "status": "supported",
            "answer": "30 days",
            "citations": [
                {"chunkId": str(chunk), "documentVersionId": str(version), "excerpt": "30 days"}
            ],
        }
    )
    saved = resolve_draft(draft, (candidate,), [source], tenant, [])
    assert saved.citations[0].filename == "policy.txt"
    for wrong_tenant, wrong_candidates, excerpt in [
        (uuid4(), (candidate,), "30 days"),
        (tenant, (), "30 days"),
        (tenant, (candidate,), "60 days"),
    ]:
        bad = draft.model_copy(deep=True)
        bad.citations[0].excerpt = excerpt
        with pytest.raises(PresalesError, match="presales_invalid_citation"):
            resolve_draft(bad, wrong_candidates, [source], wrong_tenant, [])


@pytest.mark.parametrize(
    "value", ["=HYPERLINK(1)", "+cmd", "-1+2", "@SUM(A1)", " \t=1", "\n=1", "\ufeff=1"]
)
def test_export_neutralizes_formula_cells(value: str) -> None:
    assert safe_cell(value) == "'" + value
    assert safe_cell("普通中文\n第二行") == "普通中文\n第二行"


async def test_gateway_is_one_request_and_rejects_truncation_tools_and_bad_schema() -> None:
    requests = []
    reply = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"content": json.dumps(draft_payload())},
            }
        ]
    }

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.example/v1",
            api_key=SecretStr("test-secret"),
            model_name="contract-test",
        ),
        transport=httpx.MockTransport(respond),
    )
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="客户要求"), sources=[], evidence=[]
    )
    output = await gateway.generate(payload)
    assert output.draft.status == "insufficient_evidence" and output.usage is None
    assert (
        len(requests) == 1 and requests[0]["tools"] == [] and requests[0]["tool_choice"] == "none"
    )
    assert len(requests[0]["messages"]) == 2
    assert (
        gateway.provenance["promptSha256"]
        == hashlib.sha256(requests[0]["messages"][0]["content"].encode()).hexdigest()
    )
    assert json.loads(requests[0]["messages"][1]["content"]) == {
        "requirement": payload.requirement.model_dump(mode="json", by_alias=True),
        "evidence": [],
    }
    for change in ["length", "tool", "approved", "multiple"]:
        reply = {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(draft_payload())},
                }
            ]
        }
        if change == "length":
            reply["choices"][0]["finish_reason"] = "length"
        elif change == "tool":
            reply["choices"][0]["message"]["tool_calls"] = [{"id": "forbidden"}]
        elif change == "approved":
            reply["choices"][0]["message"]["content"] = json.dumps(
                {**draft_payload(), "reviewState": "approved"}
            )
        else:
            reply["choices"].append(reply["choices"][0])
        with pytest.raises(PresalesError, match="presales_invalid_model_output"):
            await gateway.generate(payload)
    assert len(requests) == 5


async def test_gateway_rejects_oversized_input_before_dispatch() -> None:
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.example/v1",
            api_key=SecretStr("test-secret"),
            model_name="contract-test",
        ),
        transport=httpx.MockTransport(respond),
    )
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="客户要求"),
        sources=[],
        evidence=[{"text": "字" * 128_000}],
    )
    with pytest.raises(PresalesError, match="presales_input_too_large") as error:
        await gateway.generate(payload)
    assert error.value.provider_requests == 0 and calls == []


async def test_gateway_timeout_has_no_retry_and_deterministic_mode_never_fakes_results() -> None:
    calls = 0

    async def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("redacted-secret", request=request)

    settings = ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://model.example/v1",
        api_key=SecretStr("test-secret"),
        model_name="contract-test",
    )
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="Requirement"), sources=[], evidence=[]
    )
    with pytest.raises(PresalesError, match=r"^presales_model_timeout$"):
        await OpenAICompatiblePresalesGateway(
            settings, transport=httpx.MockTransport(timeout)
        ).generate(payload)
    assert calls == 1
    with pytest.raises(PresalesError, match="presales_model_not_configured"):
        await OpenAICompatiblePresalesGateway(ModelSettings()).generate(payload)


@pytest.mark.parametrize("route", ["primary", "fallback"])
async def test_explicit_presales_route_is_one_request_with_its_own_deadline(route: str) -> None:
    requests: list[httpx.Request] = []

    async def timeout(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("remote outcome unknown", request=request)

    model = ModelSettings(
        provider=ModelProvider.OPENAI_COMPATIBLE,
        base_url="https://primary.example/v1",
        api_key=SecretStr("primary-secret"),
        model_name="primary-model",
        model_revision="primary-revision",
        timeout_seconds=0.01,
        route_deadline_seconds=0.01,
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://fallback.example/v1",
        fallback_api_key=SecretStr("fallback-secret"),
        fallback_model_name="fallback-model",
        fallback_model_version="fallback-version",
    )
    settings = PresalesSettings.model_validate(
        {"model_route": route, "model_timeout_seconds": 120, "row_timeout_seconds": 150}
    )
    gateway = OpenAICompatiblePresalesGateway(
        model, presales_settings=settings, transport=httpx.MockTransport(timeout)
    )
    payload = GenerationInput(
        requirement=RequirementInput(key="R1", text="Requirement"), sources=[], evidence=[]
    )
    with pytest.raises(PresalesError, match="presales_model_timeout") as error:
        await gateway.generate(payload)
    assert error.value.provider_requests == 1
    assert len(requests) == 1
    assert str(requests[0].url) == f"https://{route}.example/v1/chat/completions"
    assert requests[0].headers["Authorization"] == f"Bearer {route}-secret"
    assert json.loads(requests[0].content)["model"] == f"{route}-model"
    assert requests[0].extensions["timeout"]["read"] == 120
    assert gateway.settings.route_deadline_seconds == 120
    assert gateway.model_name == f"{route}-model"
    assert gateway.provenance["configuredModelRevision"] == (
        "primary-revision" if route == "primary" else None
    )
    assert model.timeout_seconds == 0.01


def test_presales_route_rejects_missing_configuration_and_invalid_wait_budget() -> None:
    with pytest.raises(ValueError, match="fallback"):
        OpenAICompatiblePresalesGateway(
            ModelSettings(),
            presales_settings=PresalesSettings.model_validate({"model_route": "fallback"}),
        )
    with pytest.raises(ValueError, match="model_timeout_seconds"):
        PresalesSettings.model_validate({"model_timeout_seconds": 120, "row_timeout_seconds": 90})
    with pytest.raises(ValueError):
        PresalesSettings.model_validate({"model_route": "automatic"})
