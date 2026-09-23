from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from scripts.evaluate_presales_gateway import collect

from enterprise_doc_core.config import ModelProvider, ModelSettings


async def test_gateway_trial_preserves_failed_output_and_refuses_overwrite(tmp_path: Path) -> None:
    dataset = Path("evaluation/presales_quality_holdout_v2.json")
    output = tmp_path / "run.json"
    calls = []

    async def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        calls.append(sent)
        assert "referenceAnswer" not in request.content.decode()
        assert sent["model"] == "test-model"
        assert sent["tools"] == []
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            },
        )

    settings = ModelSettings(
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://model.invalid/v1",
        fallback_api_key=SecretStr("secret-not-for-output"),
        fallback_model_name="test-model",
    )
    report = await collect(dataset, output, settings, transport=httpx.MockTransport(respond))
    assert report["status"] == "collected" and len(calls) == 6
    assert all(
        r["state"] == "failed" and r["providerRequests"] == 1 for r in report["observations"]
    )
    assert all(
        r["traces"][0]["response"]["usage"]["total_tokens"] == 11 for r in report["observations"]
    )
    assert "secret-not-for-output" not in output.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        await collect(dataset, output, settings, transport=httpx.MockTransport(respond))
    assert len(calls) == 6


async def test_gateway_trial_records_only_safe_transport_type_and_no_retry(tmp_path: Path) -> None:
    output = tmp_path / "failed-transport.json"
    calls = 0

    async def fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private-token-and-host-do-not-record", request=request)

    settings = ModelSettings(
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://model.invalid/v1",
        fallback_api_key=SecretStr("private-api-key"),
        fallback_model_name="test-model",
    )
    report = await collect(
        Path("evaluation/presales_quality_holdout_v3.json"),
        output,
        settings,
        transport=httpx.MockTransport(fail),
    )
    assert calls == 6
    for row in report["observations"]:
        assert row["state"] == "failed"
        assert row["errorCode"] == "presales_model_transport_error"
        assert row["providerRequests"] == 1
        trace = row["traces"][0]
        assert trace["transportFailure"] == {
            "type": "ConnectError",
            "phase": "awaiting_response_headers",
        }
        assert "response" not in trace and "httpStatus" not in trace
    stored = output.read_text(encoding="utf-8")
    assert "private-token-and-host-do-not-record" not in stored
    assert "private-api-key" not in stored


async def test_cancelled_collection_records_interruption_without_retry(tmp_path: Path) -> None:
    output = tmp_path / "cancelled.json"
    calls = 0

    async def wait_for_response(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(60)
        raise AssertionError("request should be cancelled")

    settings = ModelSettings(
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://model.invalid/v1",
        fallback_api_key=SecretStr("private-api-key"),
        fallback_model_name="test-model",
    )
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await collect(
                Path("evaluation/presales_quality_holdout_v3.json"),
                output,
                settings,
                transport=httpx.MockTransport(wait_for_response),
            )
    report = json.loads(output.read_bytes())
    assert report["status"] == "interrupted"
    assert calls == len(report["observations"]) == 1
    row = report["observations"][0]
    assert row["state"] == "interrupted"
    assert row["traces"][0]["transportFailure"] == {
        "type": "CancelledError",
        "phase": "awaiting_response_headers",
    }


async def test_partial_response_failure_retains_status_not_body_and_closes(tmp_path: Path) -> None:
    streams = []

    class InterruptedBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"partial-private-body"
            raise httpx.ReadError("private-exception-text")

        async def aclose(self) -> None:
            self.closed = True

    async def respond(request: httpx.Request) -> httpx.Response:
        stream = InterruptedBody()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    settings = ModelSettings(
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url="https://model.invalid/v1",
        fallback_api_key=SecretStr("private-api-key"),
        fallback_model_name="test-model",
    )
    output = tmp_path / "partial.json"
    report = await collect(
        Path("evaluation/presales_quality_holdout_v3.json"),
        output,
        settings,
        transport=httpx.MockTransport(respond),
    )
    assert len(streams) == 6 and all(stream.closed for stream in streams)
    for row in report["observations"]:
        assert row["state"] == "failed" and row["providerRequests"] == 1
        trace = row["traces"][0]
        assert trace["httpStatus"] == 200 and "response" not in trace
        assert trace["transportFailure"] == {
            "type": "ReadError",
            "phase": "reading_response_body",
        }
    stored = output.read_text(encoding="utf-8")
    assert "partial-private-body" not in stored and "private-exception-text" not in stored
