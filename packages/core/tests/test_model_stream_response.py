from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from enterprise_doc_core.agents import (
    GroundedModelRequest,
    ModelTimeoutError,
    OpenAICompatibleChatGateway,
)
from enterprise_doc_core.config import ModelSettings
from enterprise_doc_core.model_response import ModelResponseError, OpenAIResponseReader
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput
from enterprise_doc_core.presales.settings import PresalesSettings


def sse(content, *, usage=True, ending=True, model="served-model"):
    events = [
        {
            "id": "response-1",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
        }
    ]
    if usage:
        events.append(
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }
        )
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + (
        b"data: [DONE]\n\n" if ending else b""
    )


def settings(**values):
    return ModelSettings.model_validate(
        {
            "provider": "openai_compatible",
            "base_url": "https://primary.invalid/v1",
            "api_key": "test",
            "model_name": "test-model",
            "streaming": True,
            **values,
        }
    )


def agent_request():
    return GroundedModelRequest.model_validate_json(
        json.dumps(
            {
                "task_type": "question_answer",
                "user_input": "请核对证据",
                "evidence": [],
                "behavior_versions": {
                    "graph_version": "m4.v1",
                    "prompt_version": "m4.v9",
                    "tool_schema_version": "m4.v1",
                },
            }
        )
    )


def presales_request():
    version = uuid4()
    return GenerationInput.model_validate(
        {
            "requirement": {"key": "R1", "text": "核对保留期"},
            "sources": [
                {
                    "versionId": str(version),
                    "documentId": str(uuid4()),
                    "generationId": str(uuid4()),
                    "filename": "contract.txt",
                    "applicability": "本订单",
                    "versionNumber": 1,
                    "latestVersionNumber": 1,
                    "contentSha256": "a" * 64,
                }
            ],
            "evidence": [
                {
                    "chunkId": str(uuid4()),
                    "documentVersionId": str(version),
                    "text": "报告保留期为30天。",
                }
            ],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("line_end", [b"\r\n", b"\n", b"\r"])
async def test_stream_reassembles_split_unicode_and_preserves_usage_without_reasoning(line_end):
    wire = (
        b": heartbeat\r\n\r\n"
        + b"".join(
            b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\r\n\r\n"
            for event in [
                {
                    "id": "response-1",
                    "model": "test-model",
                    "choices": [
                        {"index": 0, "delta": {"reasoning_content": "private", "content": "中"}}
                    ],
                },
                {"choices": [{"index": 0, "delta": {"content": "文"}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
            ]
        )
        + b"data: [DONE]\r\n\r\n"
    )

    wire = wire.replace(b"\r\n", line_end)

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for byte in wire:
                yield bytes([byte])

        async def aclose(self):
            self.closed = True

    stream = Stream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                stream=stream,
                headers={"content-type": "text/event-stream", "x-oneapi-request-id": "request-1"},
            )
        )
    ) as client:
        reader = OpenAIResponseReader(streaming=True, max_bytes=4096)
        async with client.stream("POST", "https://model.invalid/v1/chat/completions") as response:
            result = await reader.read(response)
    assert stream.closed
    payload = result.json()
    assert payload["choices"][0]["message"]["content"] == "中文"
    assert payload["usage"]["total_tokens"] == 30
    assert payload["model"] == "test-model"
    assert "private" not in result.text
    assert reader.accounting_response.json()["usage"]["total_tokens"] == 30
    assert result.headers["x-oneapi-request-id"] == "request-1"


@pytest.mark.parametrize(
    "wire,code",
    [
        (sse("partial", ending=False), "incomplete_model_stream"),
        (b'data: {"error":{"message":"secret"}}\n\n', "model_stream_upstream_error"),
        (b"data: broken\n\n", "invalid_model_stream"),
        (b"data: [DONE]\n\n", "incomplete_model_stream"),
        (b"x" * 4097, "model_response_too_large"),
    ],
)
async def test_stream_rejects_incomplete_invalid_and_oversized_frames(wire, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=wire, headers={"content-type": "text/event-stream"}
            )
        )
    ) as client:
        reader = OpenAIResponseReader(streaming=True, max_bytes=4096)
        async with client.stream("POST", "https://model.invalid") as response:
            with pytest.raises(ModelResponseError) as caught:
                await reader.read(response)
        assert caught.value.code == code
        assert "secret" not in str(caught.value)


async def test_agent_stream_keeps_bounded_schema_repair_and_usage():
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        answer = {
            "outcome": "refusal",
            "task_type": "question_answer",
            "refusal_reason": "insufficient_evidence",
            "answer_text": None,
            "structured_fields": None,
            "citations": [],
            "risk_hint": None,
        }
        content = "bad-json" if len(requests) == 1 else json.dumps(answer)
        return httpx.Response(
            200, content=sse(content), headers={"content-type": "text/event-stream"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await OpenAICompatibleChatGateway(
            settings=settings(reasoning_effort="high"), client=client
        ).generate(agent_request())
    assert len(requests) == 2 and result.repaired
    assert result.telemetry.provider_request_count == 2 and result.telemetry.total_tokens == 60
    assert all(
        b["stream"]
        and b["stream_options"] == {"include_usage": True}
        and b["reasoning_effort"] == "high"
        for b in requests
    )


@pytest.mark.parametrize("route", ["primary", "fallback"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_presales_stream_is_selected_independently_and_citations_stay_bound(route, enabled):
    async def respond(request):
        body = json.loads(request.content)
        assert body["stream"] is enabled
        offered = json.loads(body["messages"][1]["content"])
        content = json.dumps(
            {
                "status": "supported",
                "answer": "报告保留期为30天。",
                "prerequisites": [],
                "citations": [{"citationId": offered["evidence"][0]["citationId"]}],
            }
        )
        if enabled:
            assert body["stream_options"] == {"include_usage": True}
            return httpx.Response(
                200, content=sse(content), headers={"content-type": "text/event-stream"}
            )
        assert "stream_options" not in body
        return httpx.Response(
            200,
            json={
                "model": "served-model",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            },
        )

    config = settings(
        streaming=enabled if route == "primary" else not enabled,
        fallback_streaming=enabled if route == "fallback" else not enabled,
        fallback_provider="openai_compatible",
        fallback_base_url="https://fallback.invalid/v1",
        fallback_api_key="test",
        fallback_model_name="fallback-model",
    )
    result = await OpenAICompatiblePresalesGateway(
        config,
        presales_settings=PresalesSettings(model_route=route),
        transport=httpx.MockTransport(respond),
    ).generate(presales_request())
    assert result.draft.citations[0].excerpt == "报告保留期为30天。"


@pytest.mark.parametrize("gateway_kind", ["agent", "presales"])
async def test_heartbeat_cannot_extend_total_deadline_and_stream_closes(gateway_kind):
    class Heartbeats(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                yield b": waiting\n\n"
                await asyncio.sleep(0.005)

        async def aclose(self):
            self.closed = True

    stream = Heartbeats()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )
    config = settings(timeout_seconds=0.03)
    if gateway_kind == "agent":
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ModelTimeoutError):
                await OpenAICompatibleChatGateway(settings=config, client=client).generate(
                    agent_request()
                )
    else:
        with pytest.raises(PresalesError) as caught:
            await OpenAICompatiblePresalesGateway(config, transport=transport).generate(
                presales_request()
            )
        assert caught.value.code == "presales_model_timeout"
    assert stream.closed


async def test_presales_interrupted_stream_preserves_reported_usage_and_ids():
    class Broken(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (
                b'data: {"id":"response-1","model":"served-model",'
                b'"choices":[],"usage":{"total_tokens":42}}\n\n'
            )
            raise httpx.ReadError("private transport detail")

        async def aclose(self):
            self.closed = True

    stream = Broken()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream", "x-oneapi-request-id": "request-1"},
        )
    )
    with pytest.raises(PresalesError) as caught:
        await OpenAICompatiblePresalesGateway(settings(), transport=transport).generate(
            presales_request()
        )
    assert caught.value.code == "presales_model_transport_error"
    assert caught.value.usage["total_tokens"] == 42
    assert caught.value.provider_response_id == "response-1"
    assert caught.value.provider_request_id == "request-1"
    assert "private" not in str(caught.value)
    assert stream.closed


@pytest.mark.parametrize(
    "events",
    [
        [{"choices": [{"index": 1, "delta": {"content": "wrong"}}]}],
        [{"choices": [{"index": 0, "delta": {"tool_calls": [{}]}}]}],
        [{"choices": [{"index": 0, "delta": {"content": []}}]}],
        [{"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}],
        [{"model": "one", "choices": []}, {"model": "two", "choices": []}],
        [{"id": "one", "choices": []}, {"id": "two", "choices": []}],
        [
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [{"index": 0, "delta": {"content": "late"}}]},
        ],
    ],
)
async def test_stream_rejects_choice_identity_tool_and_completion_drift(events):
    wire = (
        b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        + b"data: [DONE]\n\n"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=wire, headers={"content-type": "text/event-stream"}
            )
        )
    ) as client:
        async with client.stream("POST", "https://model.invalid") as response:
            with pytest.raises(ModelResponseError):
                await OpenAIResponseReader(streaming=True, max_bytes=4096).read(response)


async def test_stream_without_usage_keeps_unknown_accounting():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=sse("{}", usage=False), headers={"content-type": "text/event-stream"}
            )
        )
    ) as client:
        reader = OpenAIResponseReader(streaming=True, max_bytes=4096)
        async with client.stream("POST", "https://model.invalid") as response:
            result = await reader.read(response)
    assert "usage" not in result.json()
    assert "usage" not in reader.accounting_response.json()
