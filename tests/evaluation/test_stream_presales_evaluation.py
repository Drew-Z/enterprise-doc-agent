import json
from pathlib import Path

import httpx
import pytest
from scripts.evaluate_presales_gateway import collect, load_route_settings
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
