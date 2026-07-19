from __future__ import annotations

import pytest
from pydantic import ValidationError

from enterprise_doc_core.evaluation import (
    EvaluationReport,
    LoadReport,
    build_percentile_summary,
    nearest_rank_percentile,
)


def test_nearest_rank_percentiles_are_stable_for_small_samples() -> None:
    values = [10.0, 20.0, 30.0, 40.0]

    assert nearest_rank_percentile(values, 0.5) == 20.0
    assert nearest_rank_percentile(values, 0.95) == 40.0
    assert nearest_rank_percentile([], 0.99) is None
    assert build_percentile_summary(values) == {
        "sample_count": 4,
        "min_ms": 10.0,
        "p50_ms": 20.0,
        "p95_ms": 40.0,
        "p99_ms": 40.0,
        "max_ms": 40.0,
    }

    with pytest.raises(ValueError, match="quantile"):
        nearest_rank_percentile(values, 1.1)


def test_evaluation_report_requires_a_dataset_hash_and_explicit_status() -> None:
    report = EvaluationReport(
        suite="m5-unified",
        status="passed",
        dataset_version="m5-v1",
        dataset_sha256="a" * 64,
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
    )

    assert report.status == "passed"
    with pytest.raises(ValidationError):
        EvaluationReport(
            suite="m5-unified",
            status="passed",
            dataset_version="m5-v1",
            dataset_sha256="not-a-hash",
            started_at="2026-07-19T00:00:00+00:00",
            completed_at="2026-07-19T00:00:01+00:00",
        )


def test_load_report_keeps_targets_separate_from_measured_results() -> None:
    report = LoadReport(
        scenario="health",
        status="passed",
        started_at="2026-07-19T00:00:00+00:00",
        completed_at="2026-07-19T00:00:01+00:00",
        duration_seconds=1,
        completed_requests=10,
        successful_requests=10,
        failed_requests=0,
        error_rate=0,
        throughput_requests_per_second=10,
        bottleneck="No bottleneck inferred from this bounded run.",
        capacity_conclusion="Local contract baseline only.",
        targets={"p95_ms": 250},
        measured={"p95_ms": 12.5},
    )

    assert report.targets["p95_ms"] == 250
    assert report.measured["p95_ms"] == 12.5
