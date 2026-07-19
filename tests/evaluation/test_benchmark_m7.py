from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.benchmark_m7 import _report_command, run_benchmark

from enterprise_doc_core.evaluation import verify_report_payload

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_fallback_benchmark_checks_route_state_not_only_output() -> None:
    report = await run_benchmark(
        dataset_path=ROOT / "evaluation/m7_model_benchmark_v1.json",
        scenario="fallback-contract",
        iterations=4,
    )

    assert report.status == "passed"
    assert report.fallback_count == 4
    assert report.breaker_state == "open"
    assert report.targets["fallback_count"] == 4
    assert report.citation_validity["citation_precision"] == 1.0
    assert report.provider_health["primary"].available is False
    assert report.provider_health["primary"].provider == "synthetic"
    assert report.provider_health["primary"].model_name == "timeout-gateway"
    assert report.provider_health["primary"].error_code == "healthcheck_not_supported"
    assert report.provider_health["fallback"].available is True
    assert report.provider_health["fallback"].embedding_dimension == 8
    assert report.cost_metadata.source == "not_available"
    assert report.cost_metadata.estimated_cost is None
    assert report.cost_metadata.limitation
    assert report.route["primary_provider"] == "synthetic"
    assert report.route["fallback_provider"] == "deterministic"
    assert report.provenance.input_sha256 == report.dataset_sha256
    assert verify_report_payload(report.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_deterministic_benchmark_reports_model_health_identity() -> None:
    report = await run_benchmark(
        dataset_path=ROOT / "evaluation/m7_model_benchmark_v1.json",
        scenario="deterministic",
        iterations=2,
    )

    primary = report.provider_health["primary"]
    assert primary.available is True
    assert primary.provider == "deterministic"
    assert primary.model_name == "deterministic-grounded"
    assert primary.model_version == "m7.benchmark.v1"
    assert primary.context_window_tokens == 8192
    assert primary.embedding_dimension == 8


@pytest.mark.asyncio
async def test_benchmark_rejects_an_empty_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "empty.json"
    dataset.write_text(json.dumps({"version": "empty", "cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least one case"):
        await run_benchmark(
            dataset_path=dataset,
            scenario="deterministic",
            iterations=1,
        )


def test_benchmark_command_redacts_dataset_and_report_paths(tmp_path: Path) -> None:
    args = type(
        "Args",
        (),
        {
            "dataset": tmp_path / "private-dataset.json",
            "scenario": "deterministic",
            "iterations": 2,
            "report_path": tmp_path / "private-report.json",
        },
    )()

    encoded = " ".join(_report_command(args))
    assert str(tmp_path) not in encoded
    assert "<dataset>" in encoded
    assert "<report-path>" in encoded
