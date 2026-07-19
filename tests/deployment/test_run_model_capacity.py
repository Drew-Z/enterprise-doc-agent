from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import scripts.run_model_capacity as capacity
from scripts.run_model_capacity import (
    ModelCapacityError,
    execute_stream_request,
    validate_model_capacity_config,
)


def _config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "base_url": "http://model.test",
        "metrics_url": "http://model.test/metrics",
        "model": "test/model",
        "model_revision": "a" * 40,
        "quantization": "awq",
        "api_key_env": "OPENAI_API_KEY",
        "repetitions": 2,
        "prompts": ["Summarize the evidence."],
        "phases": [
            {"name": "warmup", "requests": 1, "concurrency": 1, "max_tokens": 8},
            {"name": "steady_state", "requests": 2, "concurrency": 2, "max_tokens": 8},
            {"name": "burst", "requests": 3, "concurrency": 3, "max_tokens": 8},
            {"name": "recovery", "requests": 1, "concurrency": 1, "max_tokens": 8},
        ],
        "prometheus": {
            "base_url": "http://prometheus.test",
            "queries": {name: f"test_{name}" for name in capacity.REQUIRED_TELEMETRY},
        },
        "nvidia_smi": {"enabled": True, "interval_seconds": 1},
    }


def test_model_capacity_config_requires_fixed_metadata_and_phases() -> None:
    normalized = validate_model_capacity_config(_config())
    assert normalized["model_revision"] == "a" * 40

    config = _config()
    config["model_revision"] = ""
    with pytest.raises(ModelCapacityError, match="model_revision"):
        validate_model_capacity_config(config)

    config = _config()
    config["phases"] = config["phases"][:-1]  # type: ignore[index]
    with pytest.raises(ModelCapacityError, match="recovery"):
        validate_model_capacity_config(config)


async def test_stream_request_measures_ttft_tpot_from_exact_usage() -> None:
    body = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"A"}}]}',
            'data: {"choices":[{"delta":{"content":"B"}}]}',
            'data: {"choices":[],"usage":{"completion_tokens":2}}',
            "data: [DONE]",
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["stream_options"] == {"include_usage": True}
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://model.test"
    ) as client:
        sample = await execute_stream_request(
            client,
            model="test/model",
            prompt="test",
            max_tokens=8,
            temperature=0,
            api_key="token",
        )

    assert sample.success is True
    assert sample.output_tokens == 2
    assert sample.ttft_ms is not None
    assert sample.tpot_ms is not None
    assert sample.token_count_source == "usage"


def test_nvidia_parser_hashes_gpu_uuid() -> None:
    parsed = capacity._parse_nvidia_output(
        "2026/07/20 10:00:00.000, 0, GPU-secret-id, NVIDIA L4, 555.42, 72, 1024, 23034\n"
    )
    assert parsed[0]["utilization_percent"] == 72
    assert parsed[0]["uuid_sha256"] != "GPU-secret-id"


def test_example_model_capacity_config_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    config = capacity.load_model_capacity_config(
        root / "infra" / "capacity" / "model-capacity.example.yaml"
    )
    assert [phase["name"] for phase in config["phases"]] == list(capacity.REQUIRED_PHASES)


async def test_model_capacity_builds_validator_accepted_external_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_model_phase(
        client: object, **kwargs: object
    ) -> tuple[list[capacity.ModelRequestSample], float]:
        return [
            capacity.ModelRequestSample(
                success=True,
                status_code=200,
                duration_ms=100,
                ttft_ms=20,
                tpot_ms=10,
                output_tokens=8,
                token_count_source="usage",
            )
        ], 0.1

    async def fake_fetch_metrics(*, output_path: Path, **kwargs: object) -> tuple[Path, None]:
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            output_path.write_text, "vllm:num_requests_running 1\n", encoding="utf-8"
        )
        return output_path, None

    async def fake_query_prometheus(
        *, output_dir: Path, **kwargs: object
    ) -> tuple[dict[str, list[float]], list[Path], list[str]]:
        paths: list[Path] = []
        observations: dict[str, list[float]] = {}
        for name in capacity.REQUIRED_TELEMETRY:
            path = output_dir / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"status":"success"}\n', encoding="utf-8")
            paths.append(path)
            observations[name] = [40.0, 50.0]
        return observations, paths, []

    async def fake_nvidia_smi(**kwargs: object) -> tuple[list[dict[str, object]], list[str]]:
        return [{"name": "test-gpu", "utilization_percent": 50.0}], []

    monkeypatch.setattr(capacity, "run_model_phase", fake_run_model_phase)
    monkeypatch.setattr(capacity, "_fetch_vllm_metrics", fake_fetch_metrics)
    monkeypatch.setattr(capacity, "_query_prometheus", fake_query_prometheus)
    monkeypatch.setattr(capacity, "_sample_nvidia_smi", fake_nvidia_smi)
    monkeypatch.setattr(
        capacity.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="a" * 40 + "\n"),
    )

    report = await capacity.run_model_capacity(
        root=tmp_path,
        config=validate_model_capacity_config(_config()),
        output_dir=tmp_path / "model-capacity",
        external_execution=True,
        provider="test-cloud",
        region="test-1",
        cluster="test-gpu-cluster",
        image_digest="sha256:" + "b" * 64,
        operator="test-operator",
    )

    assert report["status"] == "passed"
    assert report["measurements"]["telemetry"]["gpu"]["sample_count"] == 16
    assert len(report["artifacts"]) == 59
