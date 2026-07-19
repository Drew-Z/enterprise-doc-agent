from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.benchmark_m7 import run_benchmark

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
