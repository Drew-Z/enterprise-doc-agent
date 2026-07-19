from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.run_application_capacity as capacity
from scripts.load_m5 import RequestSample, build_report
from scripts.run_application_capacity import (
    ApplicationCapacityError,
    summarize_prometheus_payload,
    validate_capacity_config,
)


def _config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "scenario": "ready",
        "base_url": "http://127.0.0.1:8000",
        "repetitions": 2,
        "target_p95_ms": 250,
        "phases": [
            {"name": "ramp", "requests": 10, "concurrency": 1},
            {"name": "steady_state", "requests": 20, "concurrency": 2},
            {"name": "burst", "requests": 30, "concurrency": 3},
            {"name": "recovery", "requests": 10, "concurrency": 1},
        ],
        "prometheus": {
            "base_url": "http://127.0.0.1:9090",
            "step_seconds": 5,
            "queries": {name: f"test_{name}" for name in capacity.REQUIRED_TELEMETRY},
        },
    }


def test_capacity_config_requires_all_phases_and_telemetry_queries() -> None:
    config = _config()
    normalized = validate_capacity_config(config)
    assert normalized["repetitions"] == 2

    config["phases"] = config["phases"][:-1]  # type: ignore[index]
    with pytest.raises(ApplicationCapacityError, match="recovery"):
        validate_capacity_config(config)

    config = _config()
    del config["prometheus"]["queries"]["queue"]  # type: ignore[index]
    with pytest.raises(ApplicationCapacityError, match="queue"):
        validate_capacity_config(config)


def test_prometheus_summary_rejects_identifier_labels_and_ignores_nan() -> None:
    payload = {
        "status": "success",
        "data": {
            "result": [{"metric": {"job": "api"}, "values": [[1, "10"], [2, "NaN"], [3, "30"]]}]
        },
    }
    assert summarize_prometheus_payload(payload) == {
        "sample_count": 2,
        "min": 10.0,
        "average": 20.0,
        "p95": 30.0,
        "max": 30.0,
    }

    payload["data"]["result"][0]["metric"]["tenant_id"] = "tenant"  # type: ignore[index]
    with pytest.raises(ApplicationCapacityError, match="tenant_id"):
        summarize_prometheus_payload(payload)


def test_image_digest_parser_requires_immutable_digest() -> None:
    assert capacity._parse_image_digests(["api=sha256:" + "a" * 64]) == {
        "api": "sha256:" + "a" * 64
    }
    with pytest.raises(ApplicationCapacityError, match="image-digest"):
        capacity._parse_image_digests(["api=latest"])


def test_example_capacity_config_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    config = capacity.load_capacity_config(
        root / "infra" / "capacity" / "application-capacity.example.yaml"
    )
    assert [phase["name"] for phase in config["phases"]] == list(capacity.REQUIRED_PHASES)
    assert json.dumps(config)


async def test_capacity_matrix_builds_validator_accepted_external_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_execute_load(args: object) -> tuple[object, list[RequestSample]]:
        sample = RequestSample(duration_ms=10, success=True, status_code=200)
        return (
            build_report(
                scenario="ready",
                requests=1,
                concurrency=1,
                base_url="http://test",
                samples=[sample],
                duration_seconds=0.1,
                started_at="2026-07-20T00:00:00+00:00",
                completed_at="2026-07-20T00:00:01+00:00",
                target_p95_ms=250,
            ),
            [sample],
        )

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
            observations[name] = [25.0, 30.0]
        return observations, paths, []

    monkeypatch.setattr(capacity, "execute_load", fake_execute_load)
    monkeypatch.setattr(capacity, "_query_prometheus", fake_query_prometheus)
    monkeypatch.setattr(
        capacity.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="a" * 40 + "\n"),
    )

    report = await capacity.run_capacity_matrix(
        root=tmp_path,
        config=validate_capacity_config(_config()),
        output_dir=tmp_path / "capacity",
        external_execution=True,
        provider="test-cloud",
        region="test-1",
        cluster="test-cluster",
        image_digests={"api": "sha256:" + "b" * 64},
        operator="test-operator",
    )

    assert report["status"] == "passed"
    assert report["measurements"]["telemetry"]["queue"]["sample_count"] == 16
    assert len(report["artifacts"]) == 74
