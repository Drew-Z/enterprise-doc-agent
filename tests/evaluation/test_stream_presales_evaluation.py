import hashlib
import json
from pathlib import Path

import httpx
import pytest
from scripts.evaluate_presales_gateway import RecordingTransport, collect, load_route_settings
from scripts.score_presales_gateway import score

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("expanded_framing", [False, True])
async def test_stream_evaluation_preserves_original_output_usage_and_route(
    tmp_path, expanded_framing
):
    env = tmp_path / "provider.env"
    env.write_text(
        "FALLBACK_BASE_URL=https://model.invalid/v1\nFALLBACK_API_KEY=test\nFALLBACK_MODEL_NAME=test-model\nFALLBACK_STREAMING=true\n",
        encoding="utf-8",
    )
    settings = load_route_settings(env, "fallback")
    assert settings.fallback_streaming and not settings.streaming
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        draft = {
            "prerequisites": [],
            "status": "insufficient_evidence",
            "answer": "需要补充资料。",
            "missingInformation": ["请提供证明。"],
            "citations": [],
        }
        event = {
            "id": "response-test",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": json.dumps(draft, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ],
        }
        usage = {
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        content = (
            "".join("data: " + json.dumps(x, ensure_ascii=False) + "\n\n" for x in (event, usage))
            + "data: [DONE]\n\n"
        )
        if expanded_framing:
            content = (
                'data: {"choices":[{"index":0,"delta":{"reasoning_content":"private"}}]}\n\n' * 5000
                + content
            )
        return httpx.Response(
            200, content=content.encode(), headers={"content-type": "text/event-stream"}
        )

    dataset = ROOT / "evaluation/presales_quality_holdout_v3.json"
    run = await collect(
        dataset, tmp_path / "run.json", settings, transport=httpx.MockTransport(respond)
    )
    assert run["configuredStreaming"] is True
    result = score(dataset, ROOT / "evaluation/presales_quality_holdout_v3.gold.json", run)
    assert result["realProviderRequests"] == len(requests) == 6
    assert result["acceptedDrafts"] == 6
    assert result["usage"]["total_tokens"] == 180
    assert "private" not in json.dumps(run)


@pytest.mark.parametrize(
    ("scenario", "error_code"),
    [
        ("missing_done", "incomplete_model_stream"),
        ("invalid_event", "invalid_model_stream"),
        ("upstream_error", "model_stream_upstream_error"),
        ("wrong_content_type", "invalid_model_stream"),
        ("nonstream_invalid", "invalid_response_json"),
    ],
)
async def test_failed_recording_retains_framing_diagnostics_without_response_text(
    scenario, error_code
):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    prefix = (
        "data: "
        + json.dumps(
            {
                "choices": [{"index": 0, "delta": {"reasoning_content": "private-reasoning"}}],
                "usage": usage | {"private": "private-accounting"},
            }
        )
        + "\n\n"
    )
    suffix = {
        "missing_done": "",
        "invalid_event": "data: private-malformed-event\n\n",
        "upstream_error": 'data: {"error":{"code":"upstream_error","message":"private-error"}}\n\n',
        "wrong_content_type": "",
        "nonstream_invalid": "private-not-json",
    }[scenario]
    content = (prefix + suffix).encode()
    streaming = scenario != "nonstream_invalid"
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "application/json; private-header"
                if scenario == "wrong_content_type"
                else "text/event-stream",
                "x-debug": "private-header",
            },
        )

    recorder = RecordingTransport(httpx.MockTransport(respond), max_output_bytes=4096)
    async with httpx.AsyncClient(transport=recorder) as client:
        response = await client.post(
            "https://controlled.invalid/v1/chat/completions",
            headers={"Authorization": "Bearer private-key"},
            json={"stream": streaming, "messages": [{}, {"content": "{}"}]},
        )
    assert response.content == content  # Diagnostics must not repair or replace the wire.
    assert len(seen) == len(recorder.records) == 1
    record = recorder.records[0]
    assert record["responseFailure"] == {
        "code": error_code,
        "retryable": scenario == "upstream_error",
    }
    assert record["responseBodySha256"] == hashlib.sha256(content).hexdigest()
    assert record["decodedWireBytes"] == len(content)
    assert record["eventStreamContentType"] is (scenario != "wrong_content_type")
    assert "response" not in record
    if scenario in {"missing_done", "invalid_event", "upstream_error"}:
        assert record["observedUsage"] == usage
    else:
        assert "observedUsage" not in record
    assert "private" not in json.dumps(record)
