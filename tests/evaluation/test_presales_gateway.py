from __future__ import annotations

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
