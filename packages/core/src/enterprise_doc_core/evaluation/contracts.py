from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)
    environment: dict[str, str | int | float | bool | None]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    working_tree_dirty: bool
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    passed: bool
    measured: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    failure: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    milestone: Literal["M5"] = "M5"
    suite: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed", "blocked_external"]
    dataset_version: str = Field(min_length=1, max_length=200)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    behavior_versions: dict[str, str] = Field(default_factory=dict)
    targets: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    measured: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    summary: dict[str, int | float | bool | str | None] = Field(default_factory=dict)
    cases: list[EvaluationCase] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: ReportProvenance
    started_at: str
    completed_at: str
    owner: str = "developer"


class LoadReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    milestone: Literal["M5"] = "M5"
    scenario: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed", "blocked_external"]
    started_at: str
    completed_at: str
    duration_seconds: float = Field(ge=0)
    environment: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    workload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    completed_requests: int = Field(ge=0)
    successful_requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    throughput_requests_per_second: float = Field(ge=0)
    latency_ms: dict[str, float | int | None] = Field(default_factory=dict)
    errors_by_status: dict[str, int] = Field(default_factory=dict)
    terminal_status_counts: dict[str, int] = Field(default_factory=dict)
    resource_saturation: dict[str, Any] = Field(default_factory=dict)
    bottleneck: str
    capacity_conclusion: str
    targets: dict[str, float | int | str | None] = Field(default_factory=dict)
    measured: dict[str, float | int | str | None] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    provenance: ReportProvenance
    owner: str = "developer"


class FaultExperimentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    milestone: Literal["M5"] = "M5"
    experiment: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed", "blocked_external"]
    injection: dict[str, str | int | bool] = Field(default_factory=dict)
    expected: dict[str, str | int | bool | None] = Field(default_factory=dict)
    observed: dict[str, str | int | bool | None] = Field(default_factory=dict)
    side_effects: dict[str, int | bool | str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    provenance: ReportProvenance
    started_at: str
    completed_at: str
    owner: str = "developer"


class ModelProviderHealthSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str | None = Field(default=None, max_length=200)
    model_revision: str | None = Field(default=None, max_length=200)
    quantization: str | None = Field(default=None, max_length=100)
    context_window_tokens: int | None = Field(default=None, ge=1)
    embedding_dimension: int | None = Field(default=None, ge=1)
    error_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_health_state(self) -> ModelProviderHealthSnapshot:
        if self.available and self.error_code is not None:
            raise ValueError("available providers cannot report an error code")
        return self


class ModelCostMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["provider_reported", "calculated", "not_available"]
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    pricing_version: str | None = Field(default=None, max_length=200)
    limitation: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_cost_source(self) -> ModelCostMetadata:
        cost_fields = (
            self.currency,
            self.input_tokens,
            self.output_tokens,
            self.estimated_cost,
            self.pricing_version,
        )
        if self.source == "not_available":
            if any(value is not None for value in cost_fields):
                raise ValueError("unavailable cost metadata cannot contain measured cost fields")
            if not self.limitation:
                raise ValueError("unavailable cost metadata requires a limitation")
            return self
        if self.currency is None or self.estimated_cost is None:
            raise ValueError("reported or calculated cost requires currency and estimated_cost")
        if self.source == "calculated" and self.pricing_version is None:
            raise ValueError("calculated cost requires a pricing_version")
        return self


class ModelBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    milestone: Literal["M7"] = "M7"
    scenario: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed", "blocked_external"]
    route: dict[str, str | int | None] = Field(default_factory=dict)
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_count: int = Field(ge=0)
    latency_ms: dict[str, float | int | None] = Field(default_factory=dict)
    errors_by_code: dict[str, int] = Field(default_factory=dict)
    fallback_count: int = Field(ge=0)
    breaker_state: str
    provider_health: dict[str, ModelProviderHealthSnapshot] = Field(min_length=1)
    cost_metadata: ModelCostMetadata
    citation_validity: dict[str, int | float | bool | None] = Field(default_factory=dict)
    targets: dict[str, float | int | str | None] = Field(default_factory=dict)
    measured: dict[str, float | int | str | None] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    provenance: ReportProvenance
    started_at: str
    completed_at: str
    owner: str = "developer"


def nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    """Return a deterministic nearest-rank percentile for a finite sample."""
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def build_percentile_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "sample_count": len(values),
        "min_ms": min(values) if values else None,
        "p50_ms": nearest_rank_percentile(values, 0.50),
        "p95_ms": nearest_rank_percentile(values, 0.95),
        "p99_ms": nearest_rank_percentile(values, 0.99),
        "max_ms": max(values) if values else None,
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
