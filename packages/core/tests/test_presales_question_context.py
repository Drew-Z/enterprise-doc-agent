import json
from dataclasses import replace
from uuid import UUID

import httpx
import pytest
from packages.core.tests.test_presales_question_gateway import envelope, output, payload, settings

from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.policy_gateway import freeze_policy, restore_gateway
from enterprise_doc_core.presales.question_assessment import offer_requirement_parts
from enterprise_doc_core.presales.retrieval_selection import select_complementary_evidence
from enterprise_doc_core.presales.schemas import RequirementInput
from enterprise_doc_core.presales.settings import PresalesSettings


def candidate(number, text):
    return RetrievalCandidate(
        chunk_id=UUID(int=number),
        tenant_id=UUID(int=100),
        document_version_id=UUID(int=200),
        generation_id=UUID(int=300),
        text=text,
        start_offset=number * 1200,
        end_offset=number * 1200 + len(text),
    )


@pytest.mark.parametrize(
    "query,anchor,repeated,complement",
    [
        (
            "Does the application enforce minimum password security requirements and length?",
            "Password security requirements apply when you choose, reset or change your password.",
            "You can change your password. The new password must meet password requirements.",
            "Minimum password length: 8 characters. Maximum password length: 128 characters.",
        ),
        (
            "Does the information security program cover all operations and services in scope?",
            "The security program protects confidential information and security systems.",
            "Information security is a responsibility of the security program.",
            "The ISMS scope is limited to production resources supporting SaaS services.",
        ),
    ],
)
def test_second_evidence_adds_missing_scope_or_number_within_original_budget(
    query, anchor, repeated, complement
):
    pool = (candidate(1, anchor), candidate(2, repeated), candidate(3, complement))
    selected = select_complementary_evidence(pool, query)
    assert selected == (pool[0], pool[2])
    assert selected[0] is pool[0] and selected[1] is pool[2]


@pytest.mark.parametrize("query", ["", "语义相关的中文问题", "Retention", "yes / no"])
def test_no_new_query_coverage_preserves_hybrid_order(query):
    pool = tuple(candidate(i, "Retention policy.") for i in range(1, 4))
    assert select_complementary_evidence(pool, query) == pool[:2]
    assert select_complementary_evidence(pool[:1], query) == pool[:1]
    assert select_complementary_evidence((), query) == ()


def test_repetition_ties_and_text_outside_visible_window_do_not_win():
    first = candidate(1, "password security")
    second = candidate(2, "minimum length 8")
    repeated = candidate(3, "minimum " * 100)
    hidden = candidate(4, "x " * 900 + "minimum length requirements")
    query = "password security minimum length requirements"
    assert select_complementary_evidence((first, second, repeated, hidden), query) == (
        first,
        second,
    )
    assert select_complementary_evidence(
        (first, second, replace(second, chunk_id=UUID(int=5))), query
    ) == (first, second)


def test_candidate_pool_stays_bounded_to_existing_top_ten():
    pool = tuple(candidate(i, "password") for i in range(1, 11))
    tail = candidate(11, "minimum length")
    assert select_complementary_evidence((*pool, tail), "password minimum length") == pool[:2]


@pytest.mark.parametrize("streaming", [False, True])
async def test_v25_keeps_context_examples_options_and_subquestions_in_one_literal_unit(streaming):
    text = (
        "Standards may have limited scope. For example, PCI DSS covers payment processing. "
        "Does your program cover all operations? Explain exclusions.\nYes / No"
    )
    original = payload().model_copy(update={"requirement": RequirementInput(key="Q1", text=text)})
    calls = []

    def respond(request):
        body = json.loads(request.content)
        wire = json.loads(body["messages"][1]["content"])
        calls.append(body)
        assert len(wire["requirementParts"]) == 1
        assert wire["requirementParts"][0]["text"] == text == wire["requirement"]["text"]
        assert "背景、举例和备选答案" in body["messages"][0]["content"]
        result = envelope(output(body))
        if streaming:
            result["choices"][0]["delta"] = result["choices"][0].pop("message")
            return httpx.Response(
                200,
                text="data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=result)

    config = PresalesSettings(
        primary_question_assessment=True, question_prompt_version="presales.v25"
    )
    gateway = OpenAICompatiblePresalesGateway(
        settings().model_copy(update={"streaming": streaming}),
        presales_settings=config,
        transport=httpx.MockTransport(respond),
    )
    policy = freeze_policy(gateway, config, "auto", background=True)
    restored = restore_gateway(gateway, policy.routes[0])
    draft = (await restored.generate(original)).draft
    assert len(calls) == 1 and draft.answer.count("问题\uff1a") == 1
    assert text in draft.answer and len(draft.prerequisites) == 1
    assert restored.provenance["promptVersion"] == "presales.v25"
    legacy = OpenAICompatiblePresalesGateway(settings(), question_assessment=True)
    assert legacy.provenance["promptVersion"] == "presales.v24"
    assert (
        legacy.provenance["promptSha256"]
        == "1fbca2054286a25a43f783a011137248dded9e4e343f0333d4cec934d3ce505f"
    )
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(legacy, policy.routes[0])
    with pytest.raises(PresalesError, match="presales_execution_policy_unavailable"):
        restore_gateway(
            gateway,
            freeze_policy(
                legacy, PresalesSettings(primary_question_assessment=True), "auto", background=True
            ).routes[0],
        )
    assert len(offer_requirement_parts(original.requirement)) > 1


@pytest.mark.parametrize(
    "fault", ["omitted", "duplicate", "legacy", "foreign_citation", "english", "503"]
)
async def test_context_output_keeps_selection_language_and_single_dispatch_guards(fault):
    calls = []
    original = payload()

    def respond(request):
        calls.append(True)
        if fault == "503":
            return httpx.Response(503)
        value = output(json.loads(request.content))
        if fault == "omitted":
            value["responses"] = []
        elif fault == "duplicate":
            value["responses"] *= 2
        elif fault == "legacy":
            value["responses"][0]["requirementPartId"] = offer_requirement_parts(
                original.requirement
            )[0].requirement_part_id
        elif fault == "foreign_citation":
            value["responses"][0]["citations"] = [{"citationId": "foreign"}]
        else:
            value["responses"][0]["answer"] = "Unsupported English-only text"
        return httpx.Response(200, json=envelope(value))

    gateway = OpenAICompatiblePresalesGateway(
        settings(),
        question_assessment=True,
        question_prompt_version="presales.v25",
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(original)
    assert len(calls) == caught.value.provider_requests == 1
    assert caught.value.retryable is (fault == "503")
