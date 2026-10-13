"""Run a bounded multi-document analysis workload against an explicit 4C4G target.

The workload overlaps structured parsing and analysis generation in every phase, checks
idempotent replay, exercises cancellation, and records dependency telemetry. Local runs
remain engineering evidence; only a separately declared production-like execution may
be considered for capacity approval.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from enterprise_doc_api.documents.router import DocumentInventoryItemResponse
from enterprise_doc_api.uploads.router import (
    PresignUploadPartResponse,
    UploadSessionCompleteResponse,
    UploadSessionCreateResponse,
)
from enterprise_doc_core.analysis.execution_schemas import MatrixView, RunView
from enterprise_doc_core.analysis.schemas import SnapshotView
from enterprise_doc_core.config import FoundationSettings, ModelProvider
from enterprise_doc_core.evaluation import build_percentile_summary

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.business_capacity_telemetry import identity, quantity, timestamp
    from scripts.capacity_host_probe import ROLES, digest
    from scripts.collect_business_telemetry import ObservationPlan, ssh_snapshot
    from scripts.evaluate_analysis_holdout import (
        AnalysisApiClient,
        AnalysisEvaluationError,
        shared_budget_snapshot,
        tenant_budget_snapshot,
    )
    from scripts.validate_recovery_capacity_evidence import COMMIT_PATTERN, validate_evidence
except ModuleNotFoundError:
    from business_capacity_telemetry import identity, quantity, timestamp
    from capacity_host_probe import ROLES, digest
    from collect_business_telemetry import ObservationPlan, ssh_snapshot
    from evaluate_analysis_holdout import (
        AnalysisApiClient,
        AnalysisEvaluationError,
        shared_budget_snapshot,
        tenant_budget_snapshot,
    )
    from validate_recovery_capacity_evidence import COMMIT_PATTERN, validate_evidence

PHASES = ("ramp", "steady_state", "burst", "recovery")
REQUIRED_TELEMETRY = (
    "cpu",
    "memory",
    "database_pool",
    "database_lock",
    "queue",
    "redis",
    "object_store",
    "model",
)
FORBIDDEN_LABELS = {
    "actor_id",
    "document_id",
    "document_version_id",
    "run_id",
    "session_id",
    "tenant_id",
    "user_id",
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_SERVICES = {"api", "worker", "consumer", "web"}


class AnalysisCapacityError(ValueError):
    """Stable rejection for an unsafe or incomplete capacity plan/run."""


class CapacityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CapacityTarget(CapacityModel):
    cpu_cores: Literal[4]
    memory_bytes: Literal[4294967296]
    p95_ms: float = Field(gt=0, allow_inf_nan=False)
    max_error_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_throughput_per_second: float = Field(gt=0, allow_inf_nan=False)


class AnalysisCapacityCase(CapacityModel):
    project_id: UUID
    snapshot_id: UUID
    execution_mode: Literal["fixed", "agent"] = "fixed"
    units_per_run: int = Field(ge=1, le=30, strict=True)
    max_dispatches_per_unit: Literal[2] = 2


class ParseFixture(CapacityModel):
    path: str = Field(min_length=1, max_length=500)
    media_type: Literal[
        "text/plain",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1, le=10 * 1024**2, strict=True)


class AnalysisCapacityPhase(CapacityModel):
    name: Literal["ramp", "steady_state", "burst", "recovery"]
    analysis_runs: int = Field(ge=1, le=8, strict=True)
    parse_uploads: int = Field(ge=1, le=8, strict=True)
    concurrency: int = Field(ge=1, le=4, strict=True)
    cancel_runs: int = Field(ge=0, le=8, strict=True)
    replay_runs: int = Field(ge=0, le=8, strict=True)

    @model_validator(mode="after")
    def coherent_counts(self) -> AnalysisCapacityPhase:
        if self.cancel_runs > self.analysis_runs or self.replay_runs > self.analysis_runs:
            raise ValueError("cancel/replay counts cannot exceed analysis runs")
        return self


class PrometheusPlan(CapacityModel):
    base_url: str = Field(min_length=1, max_length=500)
    bearer_token_env: str | None = Field(default=None, max_length=128)
    step_seconds: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    queries: dict[str, str]


class AnalysisCapacityPlan(CapacityModel):
    schema_version: Literal[1]
    profile: Literal["analysis-4c4g"]
    base_url: str = Field(min_length=1, max_length=500)
    object_origins: tuple[str, ...] = Field(min_length=1, max_length=4)
    token_env: str = Field(default="ANALYSIS_CAPACITY_TOKEN", pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    repetitions: int = Field(ge=2, le=3, strict=True)
    target: CapacityTarget
    analysis_case: AnalysisCapacityCase
    parse_fixture: ParseFixture
    max_model_dispatches: int = Field(ge=1, le=200, strict=True)
    phases: tuple[AnalysisCapacityPhase, ...] = Field(min_length=4, max_length=4)
    prometheus: PrometheusPlan
    request_timeout_seconds: float = Field(default=30, gt=0, le=300)
    terminal_timeout_seconds: float = Field(default=1800, gt=0, le=7200)
    poll_seconds: float = Field(default=0.25, ge=0.05, le=10)
    max_http_requests: int = Field(default=10000, ge=1, le=20000, strict=True)

    @property
    def planned_analysis_runs(self) -> int:
        return self.repetitions * sum(item.analysis_runs for item in self.phases)

    @property
    def planned_parse_uploads(self) -> int:
        return self.repetitions * sum(item.parse_uploads for item in self.phases)

    @property
    def planned_max_dispatches(self) -> int:
        return (
            self.planned_analysis_runs
            * self.analysis_case.units_per_run
            * self.analysis_case.max_dispatches_per_unit
        )

    @model_validator(mode="after")
    def coherent_plan(self) -> AnalysisCapacityPlan:
        if tuple(item.name for item in self.phases) != PHASES:
            raise ValueError("phases must be ramp, steady_state, burst, recovery")
        if self.phases[2].cancel_runs < 1:
            raise ValueError("burst phase must exercise cancellation")
        if self.phases[3].replay_runs < 1:
            raise ValueError("recovery phase must exercise replay")
        if self.planned_max_dispatches > self.max_model_dispatches:
            raise ValueError("planned model dispatch budget exceeds max_model_dispatches")
        if set(self.prometheus.queries) != set(REQUIRED_TELEMETRY):
            raise ValueError("prometheus queries must exactly cover required telemetry")
        _origin(self.base_url)
        for value in self.object_origins:
            _origin(value)
        _origin(self.prometheus.base_url)
        return self


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("origin_invalid") from None
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
    ):
        raise ValueError("origin_invalid")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _safe_path(root: Path, value: str) -> Path:
    if "\\" in value:
        raise AnalysisCapacityError("fixture_path_invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise AnalysisCapacityError("fixture_path_invalid")
    path = (root.resolve() / Path(*pure.parts)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AnalysisCapacityError("fixture_path_invalid")
    return path


def validate_capacity_plan(payload: object, *, root: Path = ROOT) -> AnalysisCapacityPlan:
    try:
        plan = AnalysisCapacityPlan.model_validate(payload)
    except ValidationError as error:
        messages = " ".join(item["msg"] for item in error.errors())
        raise AnalysisCapacityError(messages) from None
    fixture = _safe_path(root, plan.parse_fixture.path)
    if (
        not fixture.is_file()
        or fixture.stat().st_size != plan.parse_fixture.bytes
        or hashlib.sha256(fixture.read_bytes()).hexdigest() != plan.parse_fixture.sha256
    ):
        raise AnalysisCapacityError("parse fixture integrity mismatch")
    return plan


def load_capacity_plan(path: Path, *, root: Path = ROOT) -> AnalysisCapacityPlan:
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise AnalysisCapacityError("capacity plan is missing or too large")
    try:
        payload = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix.lower() == ".json"
            else yaml.safe_load(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, yaml.YAMLError):
        raise AnalysisCapacityError("capacity plan cannot be parsed") from None
    return validate_capacity_plan(payload, root=root)


def _telemetry_summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(value for value in values if math.isfinite(value))
    percentile = build_percentile_summary(ordered)
    return {
        "sample_count": len(ordered),
        "min": min(ordered) if ordered else None,
        "average": sum(ordered) / len(ordered) if ordered else None,
        "p95": percentile["p95_ms"],
        "max": max(ordered) if ordered else None,
    }


def _prometheus_values(payload: object) -> list[float]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise AnalysisCapacityError("prometheus_response_invalid")
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise AnalysisCapacityError("prometheus_response_invalid")
    values: list[float] = []
    for series in result:
        if not isinstance(series, dict) or not isinstance(series.get("metric", {}), dict):
            raise AnalysisCapacityError("prometheus_response_invalid")
        labels = {str(key).lower() for key in series["metric"]}
        forbidden = labels & FORBIDDEN_LABELS
        if forbidden:
            raise AnalysisCapacityError(
                "prometheus_high_cardinality_labels:" + ",".join(sorted(forbidden))
            )
        samples = series.get("values")
        if not isinstance(samples, list):
            samples = [series.get("value")] if isinstance(series.get("value"), list) else []
        for sample in samples:
            if not isinstance(sample, list) or len(sample) != 2:
                continue
            try:
                value = float(sample[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                values.append(value)
    return values


def _headroom(telemetry: dict[str, dict[str, float | int | None]]) -> float:
    saturation = [
        float(value)
        for name in ("cpu", "memory", "database_pool")
        if isinstance((value := telemetry.get(name, {}).get("p95")), (int, float))
    ]
    return round(max(0.0, 100.0 - max(saturation)), 6) if saturation else 0.0


def _bottleneck(telemetry: dict[str, dict[str, float | int | None]], *, failed: int) -> str:
    if failed:
        return "Request failures or terminal timeouts were observed."
    measured = {
        name: float(value)
        for name, summary in telemetry.items()
        if isinstance((value := summary.get("p95")), (int, float))
    }
    if not measured:
        return "No dependency telemetry was measured; the bottleneck is unknown."
    name, value = max(measured.items(), key=lambda item: item[1])
    return f"Highest measured telemetry p95 was {name} at {value:.3f}."


def build_capacity_report(
    *,
    plan: AnalysisCapacityPlan,
    samples: list[dict[str, object]],
    telemetry: dict[str, dict[str, float | int | None]],
    duration_seconds: float,
    external_execution: bool,
    environment: dict[str, object],
) -> dict[str, object]:
    durations = [
        float(item["duration_ms"])
        for item in samples
        if isinstance(item.get("duration_ms"), (int, float))
    ]
    latency = build_percentile_summary(durations)
    failed = sum(item.get("status") != "passed" for item in samples)
    completed = len(samples)
    measured = {
        "p50_ms": float(latency["p50_ms"] or 0.0),
        "p95_ms": float(latency["p95_ms"] or 0.0),
        "p99_ms": float(latency["p99_ms"] or 0.0),
        "error_rate": failed / completed if completed else 1.0,
        "throughput_per_second": completed / duration_seconds if duration_seconds > 0 else 0.0,
        "completed_operations": completed,
        "failed_operations": failed,
        "headroom_percent": _headroom(telemetry),
        "bottleneck": _bottleneck(telemetry, failed=failed),
        "telemetry": telemetry,
    }
    targets = {
        "cpu_cores": plan.target.cpu_cores,
        "memory_bytes": plan.target.memory_bytes,
        "p95_ms": plan.target.p95_ms,
        "max_error_rate": plan.target.max_error_rate,
        "min_throughput_per_second": plan.target.min_throughput_per_second,
        "planned_max_model_dispatches": plan.planned_max_dispatches,
    }
    telemetry_complete = all(
        int(telemetry.get(name, {}).get("sample_count") or 0) > 0 for name in REQUIRED_TELEMETRY
    )
    thresholds_passed = (
        failed == 0
        and measured["p95_ms"] <= plan.target.p95_ms
        and measured["error_rate"] <= plan.target.max_error_rate
        and measured["throughput_per_second"] >= plan.target.min_throughput_per_second
        and telemetry_complete
    )
    status = (
        "passed"
        if external_execution and thresholds_passed
        else ("failed" if external_execution else "blocked_external")
    )
    conclusion = (
        f"Measured {completed} operations across the frozen {plan.profile} workload; "
        f"p95={measured['p95_ms']:.3f} ms, error_rate={measured['error_rate']:.6f}, "
        f"throughput={measured['throughput_per_second']:.6f}/s. "
        "It is not a production capacity claim."
    )
    return {
        "schema_version": 1,
        "evidence_type": "capacity",
        "capacity_profile": "application",
        "status": status,
        "environment": environment,
        "workload": {
            "name": plan.profile,
            "base_url": _origin(plan.base_url),
            "phases": list(PHASES),
            "repetitions": plan.repetitions,
            "planned_analysis_runs": plan.planned_analysis_runs,
            "planned_parse_uploads": plan.planned_parse_uploads,
            "overlaps_parsing_and_generation": True,
            "exercises_cancellation": True,
            "exercises_replay_recovery": True,
        },
        "targets": targets,
        "measured": measured,
        "measurements": measured,
        "capacity_conclusion": conclusion,
        "limitations": [
            (
                "A single host and measurement window, including repeated phases, "
                "is not a production capacity claim."
            ),
            (
                "The report applies only to the exact workload, target limits, "
                "source revision, and time window."
            ),
            (
                "Target limits are operator declarations unless independently "
                "verified by the runtime platform."
            ),
            (
                "Production approval still requires isolated production-like "
                "execution and workload-owner review."
            ),
        ],
        "thresholds_passed": thresholds_passed,
        "telemetry_complete": telemetry_complete,
    }


class CapacityIO:
    def __init__(self, plan: AnalysisCapacityPlan, token: str, *, correlation_id: str) -> None:
        self.plan = plan
        self.correlation_id = correlation_id
        self.api = AnalysisApiClient(
            base_url=plan.base_url,
            token=token,
            timeout_seconds=plan.request_timeout_seconds,
        )
        self.requests = 0

    async def api_request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        if self.requests >= self.plan.max_http_requests:
            raise AnalysisCapacityError("http_request_budget_exhausted")
        self.requests += 1
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-Correlation-ID"] = self.correlation_id
        try:
            return await self.api.request(*args, headers=headers, **kwargs)
        except AnalysisEvaluationError as error:
            raise AnalysisCapacityError(str(error)) from None


def _decode[T: BaseModel](model: type[T], response: httpx.Response) -> T:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise AnalysisCapacityError("api_contract_invalid") from None


async def _upload_and_wait(io: CapacityIO, *, fixture: bytes, key: str) -> dict[str, object]:
    plan = io.plan
    spec = plan.parse_fixture
    async with (
        io.api.client() as api,
        httpx.AsyncClient(
            timeout=plan.request_timeout_seconds, follow_redirects=False, trust_env=False
        ) as objects,
    ):
        created = _decode(
            UploadSessionCreateResponse,
            await io.api_request(
                api,
                "POST",
                "/api/upload-sessions",
                payload={
                    "filename": Path(spec.path).name,
                    "sizeBytes": len(fixture),
                    "mediaType": spec.media_type,
                    "sha256": spec.sha256,
                },
                headers={"Idempotency-Key": key},
                accepted=(200, 201),
            ),
        )
        session_path = f"/api/upload-sessions/{created.session_id}"
        parts: list[dict[str, object]] = []
        for number in range(1, created.expected_part_count + 1):
            content = fixture[
                (number - 1) * created.part_size_bytes : number * created.part_size_bytes
            ]
            checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
            signed = _decode(
                PresignUploadPartResponse,
                await io.api_request(
                    api,
                    "POST",
                    f"{session_path}/parts/{number}/presign",
                    payload={"sizeBytes": len(content), "checksumSha256": checksum},
                ),
            )
            parsed = urlsplit(signed.url)
            signed_origin = f"{parsed.scheme}://{parsed.netloc}"
            if (
                signed_origin not in {_origin(value) for value in plan.object_origins}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or any(
                    name.lower()
                    in {
                        "authorization",
                        "cookie",
                        "proxy-authorization",
                        "host",
                        "x-session-context",
                        "x-csrf-token",
                    }
                    for name in signed.headers
                )
            ):
                raise AnalysisCapacityError("object_request_rejected")
            response = await objects.put(signed.url, content=content, headers=signed.headers)
            if response.status_code not in {200, 201, 204} or not response.headers.get("etag"):
                raise AnalysisCapacityError("object_upload_failed")
            parts.append(
                {
                    "partNumber": number,
                    "sizeBytes": len(content),
                    "etag": response.headers["etag"],
                    "checksumSha256": checksum,
                }
            )
        completed = _decode(
            UploadSessionCompleteResponse,
            await io.api_request(api, "POST", f"{session_path}/complete", payload={"parts": parts}),
        )
        if completed.status != "completed":
            raise AnalysisCapacityError("upload_completion_failed")
        deadline = time.monotonic() + plan.terminal_timeout_seconds
        while time.monotonic() < deadline:
            inventory = await io.api_request(api, "GET", "/api/documents?limit=200")
            try:
                rows = [
                    DocumentInventoryItemResponse.model_validate(item) for item in inventory.json()
                ]
            except (ValueError, TypeError, ValidationError):
                raise AnalysisCapacityError("inventory_contract_invalid") from None
            owned = [item for item in rows if item.version_id == completed.version_id]
            if len(owned) > 1:
                raise AnalysisCapacityError("duplicate_document_version")
            if owned and owned[0].version_status == "ready":
                return {
                    "document_id": str(completed.document_id),
                    "version_id": str(completed.version_id),
                    "generation_id": str(owned[0].generation_id),
                }
            if owned and (
                owned[0].version_status == "failed" or owned[0].ingestion_status == "failed"
            ):
                raise AnalysisCapacityError("parse_failed")
            await asyncio.sleep(plan.poll_seconds)
    raise AnalysisCapacityError("parse_timeout")


async def _run_analysis(
    io: CapacityIO,
    *,
    key: str,
    cancel: bool,
    replay: bool,
) -> dict[str, object]:
    case = io.plan.analysis_case
    async with io.api.client() as client:
        created = _decode(
            RunView,
            await io.api_request(
                client,
                "POST",
                f"/api/analysis/projects/{case.project_id}/runs",
                payload={
                    "snapshotId": str(case.snapshot_id),
                    "executionMode": case.execution_mode,
                },
                headers={"Idempotency-Key": key},
                accepted=(202,),
            ),
        )
        if created.total_units != case.units_per_run:
            raise AnalysisCapacityError("analysis_unit_count_mismatch")
        if cancel:
            terminal = _decode(
                RunView,
                await io.api_request(client, "POST", f"/api/analysis/runs/{created.id}/cancel"),
            )
        else:
            deadline = time.monotonic() + io.plan.terminal_timeout_seconds
            terminal = created
            while (
                terminal.status
                not in {
                    "completed",
                    "partial",
                    "failed",
                    "cancelled",
                    "expired",
                }
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(io.plan.poll_seconds)
                terminal = _decode(
                    RunView,
                    await io.api_request(client, "GET", f"/api/analysis/runs/{created.id}"),
                )
            if terminal.status != "completed":
                raise AnalysisCapacityError("analysis_run_not_completed")
        if cancel and terminal.status != "cancelled":
            raise AnalysisCapacityError("analysis_cancel_not_observed")
        if replay:
            repeated = _decode(
                RunView,
                await io.api_request(
                    client,
                    "POST",
                    f"/api/analysis/projects/{case.project_id}/runs",
                    payload={
                        "snapshotId": str(case.snapshot_id),
                        "executionMode": case.execution_mode,
                    },
                    headers={"Idempotency-Key": key},
                    accepted=(202,),
                ),
            )
            if repeated.id != created.id or repeated.dispatches != terminal.dispatches:
                raise AnalysisCapacityError("analysis_replay_changed_run")
        matrix = _decode(
            MatrixView,
            await io.api_request(client, "GET", f"/api/analysis/runs/{created.id}/matrix"),
        )
        if len(matrix.units) != case.units_per_run:
            raise AnalysisCapacityError("analysis_matrix_denominator_mismatch")
        return {
            "run_id": str(created.id),
            "terminal_status": terminal.status,
            "dispatches": terminal.dispatches,
            "cancel_verified": cancel,
            "replay_verified": replay,
        }


async def _capacity_preflight(
    io: CapacityIO, *, settings: FoundationSettings
) -> tuple[UUID, dict[str, object]]:
    plan = io.plan
    token = os.environ.get(plan.token_env, "")
    if not token:
        raise AnalysisCapacityError("capacity_token_missing")
    if (
        settings.model.provider is not ModelProvider.OPENAI_COMPATIBLE
        or not settings.analysis.execution_enabled
        or not settings.upload.structured_documents_enabled
    ):
        raise AnalysisCapacityError("analysis_runtime_unconfigured")
    async with io.api.client() as client:
        capabilities = (await io.api_request(client, "GET", "/api/analysis/capabilities")).json()
        if not all(
            capabilities.get(name) is True
            for name in ("admissionEnabled", "executionEnabled", "structuredUploadsEnabled")
        ):
            raise AnalysisCapacityError("analysis_target_capabilities_disabled")
        response = await io.api_request(
            client,
            "GET",
            f"/api/analysis/projects/{plan.analysis_case.project_id}/snapshots",
        )
        try:
            snapshots = [SnapshotView.model_validate(item) for item in response.json()]
        except (ValueError, TypeError, ValidationError):
            raise AnalysisCapacityError("analysis_snapshot_contract_invalid") from None
        selected = [item for item in snapshots if item.id == plan.analysis_case.snapshot_id]
        if len(selected) != 1:
            raise AnalysisCapacityError("analysis_snapshot_missing")
        units = len(selected[0].payload.spec.objects) * len(selected[0].payload.spec.fields)
        if units != plan.analysis_case.units_per_run:
            raise AnalysisCapacityError("analysis_unit_count_mismatch")
        usage = (await io.api_request(client, "GET", "/api/tenant-usage")).json()
    try:
        tenant_id = UUID(str(usage["tenantId"]))
        quotas = {
            item["metric"]: item
            for item in usage["productQuotas"]
            if isinstance(item, dict) and isinstance(item.get("metric"), str)
        }
    except (KeyError, TypeError, ValueError):
        raise AnalysisCapacityError("usage_contract_invalid") from None
    required_cells = plan.planned_analysis_runs * plan.analysis_case.units_per_run
    required_bytes = plan.planned_parse_uploads * plan.parse_fixture.bytes
    if int(quotas.get("analysis_cell", {}).get("remaining", -1)) < required_cells:
        raise AnalysisCapacityError("analysis_cell_quota_insufficient")
    if int(quotas.get("document_bytes", {}).get("remaining", -1)) < required_bytes:
        raise AnalysisCapacityError("document_byte_quota_insufficient")
    budget = await shared_budget_snapshot(
        settings=settings,
        required_dispatches=plan.max_model_dispatches,
    )
    tenant_budget = await tenant_budget_snapshot(
        settings=settings,
        tenant_id=tenant_id,
        required_dispatches=plan.max_model_dispatches,
    )
    return tenant_id, {
        "tenant_id": str(tenant_id),
        "snapshot_id": str(selected[0].id),
        "source_manifest_hash": selected[0].source_manifest_hash,
        "required_analysis_cells": required_cells,
        "required_document_bytes": required_bytes,
        "shared_generation_budget": budget,
        "tenant_provider_budget": tenant_budget,
    }


async def _run_operation(
    *,
    semaphore: asyncio.Semaphore,
    phase: str,
    kind: str,
    operation: Callable[[], Awaitable[object]],
) -> dict[str, object]:
    sample: dict[str, object] = {
        "phase": phase,
        "kind": kind,
        "status": "not_run",
        "duration_ms": None,
        "error_code": None,
    }
    started = time.perf_counter()
    try:
        async with semaphore:
            sample["result"] = await operation()
        sample["status"] = "passed"
    except (AnalysisCapacityError, httpx.HTTPError, TimeoutError) as error:
        sample["status"] = "failed"
        sample["error_code"] = (
            str(error) if isinstance(error, AnalysisCapacityError) else "capacity_operation_failed"
        )
    finally:
        sample["duration_ms"] = (time.perf_counter() - started) * 1000
    return sample


async def _query_prometheus(
    *,
    plan: AnalysisCapacityPlan,
    started_at: datetime,
    completed_at: datetime,
) -> tuple[dict[str, dict[str, float | int | None]], dict[str, object]]:
    headers: dict[str, str] = {}
    if plan.prometheus.bearer_token_env:
        token = os.environ.get(plan.prometheus.bearer_token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    summaries: dict[str, dict[str, float | int | None]] = {}
    raw: dict[str, object] = {}
    async with httpx.AsyncClient(
        base_url=_origin(plan.prometheus.base_url),
        timeout=30,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    ) as client:
        for name in REQUIRED_TELEMETRY:
            try:
                response = await client.get(
                    "/api/v1/query_range",
                    params={
                        "query": plan.prometheus.queries[name],
                        "start": started_at.isoformat(),
                        "end": completed_at.isoformat(),
                        "step": plan.prometheus.step_seconds,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                values = _prometheus_values(payload)
                raw[name] = payload
            except (httpx.HTTPError, ValueError, AnalysisCapacityError) as error:
                values = []
                raw[name] = {"status": "error", "error_code": type(error).__name__}
            summaries[name] = _telemetry_summary(values)
    return summaries, raw


async def execute_capacity(
    *, plan: AnalysisCapacityPlan, settings: FoundationSettings, execution_id: str
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
    token = os.environ.get(plan.token_env, "")
    io = CapacityIO(plan, token, correlation_id=f"analysis-capacity:{execution_id}")
    _, preflight = await _capacity_preflight(io, settings=settings)
    fixture = _safe_path(ROOT, plan.parse_fixture.path).read_bytes()
    samples: list[dict[str, object]] = []
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    for repetition in range(1, plan.repetitions + 1):
        for phase in plan.phases:
            semaphore = asyncio.Semaphore(phase.concurrency)
            operations = []
            for index in range(phase.analysis_runs):
                cancel = index < phase.cancel_runs
                replay = index < phase.replay_runs
                kind = "analysis_cancel" if cancel else "analysis_replay" if replay else "analysis"
                key = f"analysis-cap-{execution_id}-r{repetition}-{phase.name}-{index}"
                operations.append(
                    _run_operation(
                        semaphore=semaphore,
                        phase=phase.name,
                        kind=kind,
                        operation=lambda key=key, cancel=cancel, replay=replay: _run_analysis(
                            io, key=key, cancel=cancel, replay=replay
                        ),
                    )
                )
            for index in range(phase.parse_uploads):
                key = f"analysis-cap-parse-{execution_id}-r{repetition}-{phase.name}-{index}"
                operations.append(
                    _run_operation(
                        semaphore=semaphore,
                        phase=phase.name,
                        kind="parse",
                        operation=lambda key=key: _upload_and_wait(io, fixture=fixture, key=key),
                    )
                )
            phase_samples = await asyncio.gather(*operations)
            for sample in phase_samples:
                sample["repetition"] = repetition
            samples.extend(phase_samples)
    completed_at = datetime.now(UTC)
    telemetry, raw_telemetry = await _query_prometheus(
        plan=plan, started_at=started_at, completed_at=completed_at
    )
    report = build_capacity_report(
        plan=plan,
        samples=samples,
        telemetry=telemetry,
        duration_seconds=time.perf_counter() - started,
        external_execution=False,
        environment={
            "name": "local-docker-4c4g",
            "external_execution": False,
            "provider": None,
            "region": None,
            "cluster": "operator-confirmed-local-docker",
            "declared_cpu_cores": plan.target.cpu_cores,
            "declared_memory_bytes": plan.target.memory_bytes,
        },
    )
    report.update(
        {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "preflight": preflight,
            "http_requests": io.requests,
        }
    )
    return report, samples, raw_telemetry


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _artifact(root: Path, path: Path, kind: str) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise AnalysisCapacityError("capacity_output_outside_repository")
    return {
        "path": resolved.relative_to(root.resolve()).as_posix(),
        "kind": kind,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


def _load_json_object(path: Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    if not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
        raise AnalysisCapacityError(f"{label}_invalid")
    try:
        payload = json.loads(path.read_bytes())
    except json.JSONDecodeError:
        raise AnalysisCapacityError(f"{label}_invalid") from None
    if not isinstance(payload, dict):
        raise AnalysisCapacityError(f"{label}_invalid")
    return payload


def _release_identity(path: Path, *, deployed_commit: str, root: Path = ROOT) -> dict[str, Any]:
    payload = _load_json_object(path, label="release_manifest", max_bytes=2 * 1024 * 1024)
    release = payload.get("release")
    images = payload.get("images")
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "passed"
        or not isinstance(release, dict)
        or release.get("commit_sha") != deployed_commit
        or any(
            not isinstance(release.get(name), str) or not release[name].strip()
            for name in ("repository", "ref", "run_id", "run_attempt", "workflow_ref")
        )
        or not isinstance(images, dict)
        or set(images) != RELEASE_SERVICES
    ):
        raise AnalysisCapacityError("release_manifest_identity_mismatch")
    digests: dict[str, str] = {}
    for service in sorted(RELEASE_SERVICES):
        item = images.get(service)
        digest_value = item.get("digest") if isinstance(item, dict) else None
        if not isinstance(digest_value, str) or DIGEST_PATTERN.fullmatch(digest_value) is None:
            raise AnalysisCapacityError("release_manifest_identity_mismatch")
        digests[service] = digest_value
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "repository": release.get("repository"),
        "ref": release.get("ref"),
        "run_id": release.get("run_id"),
        "run_attempt": release.get("run_attempt"),
        "workflow_ref": release.get("workflow_ref"),
        "commit_sha": deployed_commit,
        "image_digests": digests,
    }


def _snapshot_images(snapshot: dict[str, Any]) -> dict[str, tuple[str, str]]:
    inventory = snapshot.get("inventory")
    pods = inventory.get("pods") if isinstance(inventory, dict) else None
    if not isinstance(pods, list):
        raise AnalysisCapacityError("resource_snapshot_invalid")
    result: dict[str, tuple[str, str]] = {}
    for pod in pods:
        if not isinstance(pod, dict) or pod.get("role") not in RELEASE_SERVICES:
            continue
        containers = pod.get("containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise AnalysisCapacityError("resource_snapshot_invalid")
        configured = containers[0].get("configured_digest")
        running = containers[0].get("running_digest")
        role = str(pod["role"])
        if (
            not isinstance(configured, str)
            or DIGEST_PATTERN.fullmatch(configured) is None
            or not isinstance(running, str)
            or DIGEST_PATTERN.fullmatch(running) is None
            or role in result
        ):
            raise AnalysisCapacityError("resource_snapshot_invalid")
        result[role] = (configured, running)
    if set(result) != RELEASE_SERVICES:
        raise AnalysisCapacityError("resource_snapshot_invalid")
    return result


def validate_resource_snapshots(
    records: list[dict[str, Any]],
    *,
    plan: AnalysisCapacityPlan,
    release: dict[str, Any],
    started_at: datetime,
    completed_at: datetime,
    interval_seconds: float,
    correlation_id: str,
    expected_http_requests: int,
) -> dict[str, Any]:
    if len(records) < 2:
        raise AnalysisCapacityError("resource_snapshots_incomplete")
    expected_digests = release["image_digests"]
    identities: set[str] = set()
    snapshots: list[dict[str, Any]] = []
    correlation_sha256 = digest(correlation_id)
    api_pod_uid: str | None = None
    observed_counts: list[int] = []
    try:
        captured_values = [timestamp(str(record.get("captured_at"))) for record in records]
    except (TypeError, ValueError):
        raise AnalysisCapacityError("resource_snapshot_invalid") from None
    if captured_values != sorted(captured_values):
        raise AnalysisCapacityError("resource_snapshot_order_invalid")
    if (
        captured_values[0] > started_at
        or (started_at - captured_values[0]).total_seconds() > 60
        or captured_values[-1] < completed_at
        or (captured_values[-1] - completed_at).total_seconds() > 60
        or any(
            (right - left).total_seconds() > interval_seconds * 2.5 + 25
            for left, right in itertools.pairwise(captured_values)
        )
    ):
        raise AnalysisCapacityError("resource_snapshots_do_not_cover_workload")
    for record in records:
        if not isinstance(record, dict):
            raise AnalysisCapacityError("resource_snapshot_invalid")
        if record.get("errors") != []:
            raise AnalysisCapacityError("resource_snapshot_source_unavailable")
        inventory = record.get("inventory")
        host = record.get("host")
        if not isinstance(inventory, dict) or not isinstance(host, dict):
            raise AnalysisCapacityError("resource_snapshot_invalid")
        nodes = inventory.get("nodes")
        deployments = inventory.get("deployments")
        pods = inventory.get("pods")
        if (
            not inventory.get("cluster_sha256")
            or not inventory.get("configuration_sha256")
            or not isinstance(nodes, list)
            or len(nodes) != 1
            or not isinstance(deployments, list)
            or {item.get("role") for item in deployments if isinstance(item, dict)} != set(ROLES)
            or not isinstance(pods, list)
            or {item.get("role") for item in pods if isinstance(item, dict)} != set(ROLES)
        ):
            raise AnalysisCapacityError("resource_snapshot_identity_incomplete")
        proof = record.get("traffic_proof")
        api_pods = [item for item in pods if isinstance(item, dict) and item.get("role") == "api"]
        if len(api_pods) != 1 or not isinstance(proof, dict):
            raise AnalysisCapacityError("resource_snapshot_target_binding_incomplete")
        observations = proof.get("observations")
        if (
            proof.get("correlation_sha256") != correlation_sha256
            or proof.get("api_pod_uid_sha256") != api_pods[0].get("uid_sha256")
            or isinstance(observations, bool)
            or not isinstance(observations, int)
            or observations < 0
            or proof.get("status") != ("observed" if observations else "not_observed")
        ):
            raise AnalysisCapacityError("resource_snapshot_target_binding_invalid")
        if api_pod_uid is not None and api_pod_uid != proof["api_pod_uid_sha256"]:
            raise AnalysisCapacityError("resource_snapshot_target_binding_drift")
        api_pod_uid = str(proof["api_pod_uid_sha256"])
        observed_counts.append(observations)
        if host.get("cpu_count") != plan.target.cpu_cores:
            raise AnalysisCapacityError("resource_snapshot_cpu_mismatch")
        memory_total = host.get("memory_total_bytes")
        memory_tolerance = 512 * 1024**2
        if (
            isinstance(memory_total, bool)
            or not isinstance(memory_total, int)
            or abs(memory_total - plan.target.memory_bytes) > memory_tolerance
        ):
            raise AnalysisCapacityError("resource_snapshot_memory_mismatch")
        node = nodes[0]
        try:
            node_cpu = quantity(str(node.get("cpu_capacity"))) if isinstance(node, dict) else None
            node_memory = (
                quantity(str(node.get("memory_capacity"))) if isinstance(node, dict) else None
            )
        except (TypeError, ValueError):
            raise AnalysisCapacityError("resource_snapshot_invalid") from None
        if node_cpu != plan.target.cpu_cores:
            raise AnalysisCapacityError("resource_snapshot_cpu_mismatch")
        if node_memory is None or abs(node_memory - plan.target.memory_bytes) > memory_tolerance:
            raise AnalysisCapacityError("resource_snapshot_memory_mismatch")
        relationships = {
            (item.get("configured_digest"), item.get("running_digest"))
            for item in inventory.get("image_relationships", [])
            if isinstance(item, dict) and item.get("verified") is True
        }
        observed_images = _snapshot_images(record)
        for role, expected in expected_digests.items():
            configured, running = observed_images[role]
            if expected != running and not (
                expected == configured and (configured, running) in relationships
            ):
                raise AnalysisCapacityError("resource_snapshot_image_mismatch")
        for deployment in deployments:
            if (
                not isinstance(deployment, dict)
                or deployment.get("desired_replicas") != 1
                or deployment.get("ready_replicas") != 1
                or deployment.get("updated_replicas") != 1
                or deployment.get("generation") != deployment.get("observed_generation")
            ):
                raise AnalysisCapacityError("resource_snapshot_deployment_not_ready")
        for pod in pods:
            if (
                not isinstance(pod, dict)
                or pod.get("phase") != "Running"
                or not isinstance(pod.get("containers"), list)
                or any(
                    not container.get("ready") or container.get("last_oom")
                    for container in pod["containers"]
                    if isinstance(container, dict)
                )
            ):
                raise AnalysisCapacityError("resource_snapshot_pod_not_ready")
        identities.add(identity(record))
        snapshots.append(record)
    if len(identities) != 1:
        raise AnalysisCapacityError("resource_snapshot_identity_drift")
    final_proof = snapshots[-1].get("traffic_proof")
    if (
        not isinstance(final_proof, dict)
        or final_proof.get("status") != "observed"
        or observed_counts != sorted(observed_counts)
        or observed_counts[0] != 0
        or observed_counts[-1] != expected_http_requests
    ):
        raise AnalysisCapacityError("resource_snapshot_target_not_observed")
    return {
        "status": "verified",
        "sample_count": len(snapshots),
        "identity_sha256": next(iter(identities)),
        "samples_sha256": digest(snapshots),
        "cpu_cores": plan.target.cpu_cores,
        "memory_bytes": plan.target.memory_bytes,
        "image_digests": expected_digests,
        "first_captured_at": snapshots[0]["captured_at"],
        "last_captured_at": snapshots[-1]["captured_at"],
        "target_binding": {
            "status": "verified",
            "origin": _origin(plan.base_url),
            "correlation_sha256": correlation_sha256,
            "api_pod_uid_sha256": api_pod_uid,
            "observations": observed_counts[-1],
            "expected_http_requests": expected_http_requests,
        },
    }


async def _run_external_capacity_with_snapshots(
    *,
    plan: AnalysisCapacityPlan,
    settings: FoundationSettings,
    execution_id: str,
    ssh_host: str,
    namespace: str,
    interval_seconds: float,
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object], list[dict[str, Any]]]:
    proof_started_at = datetime.now(UTC).isoformat()
    observation = ObservationPlan(
        ssh_host=ssh_host,
        namespace=namespace,
        samples=2,
        interval_seconds=interval_seconds,
        max_run_seconds=max(120, interval_seconds + 50),
        correlation_id=f"analysis-capacity:{execution_id}",
        since_time=proof_started_at,
    )
    records = [await asyncio.to_thread(ssh_snapshot, observation)]
    workload = asyncio.create_task(
        execute_capacity(plan=plan, settings=settings, execution_id=execution_id)
    )
    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(workload), timeout=interval_seconds)
                break
            except TimeoutError:
                records.append(await asyncio.to_thread(ssh_snapshot, observation))
        records.append(await asyncio.to_thread(ssh_snapshot, observation))
    except BaseException:
        workload.cancel()
        await asyncio.gather(workload, return_exceptions=True)
        raise
    report, samples, raw_telemetry = result
    return report, samples, raw_telemetry, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute-local", action="store_true")
    parser.add_argument("--confirm-4c4g-target", action="store_true")
    parser.add_argument("--execution-id")
    parser.add_argument("--external-execution", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--region")
    parser.add_argument("--cluster")
    parser.add_argument("--deployed-commit")
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--resource-ssh-host")
    parser.add_argument("--resource-namespace", default="enterprise-doc-agent-staging")
    parser.add_argument("--resource-sample-seconds", type=float, default=30)
    parser.add_argument(
        "--operator", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator"
    )
    args = parser.parse_args()
    try:
        plan = load_capacity_plan(args.plan)
        if not args.execute_local:
            print(
                json.dumps(
                    {
                        "status": "validated",
                        "profile": plan.profile,
                        "planned_analysis_runs": plan.planned_analysis_runs,
                        "planned_parse_uploads": plan.planned_parse_uploads,
                        "planned_max_dispatches": plan.planned_max_dispatches,
                        "production_capacity_approved": False,
                    }
                )
            )
            return
        if not args.confirm_4c4g_target:
            raise AnalysisCapacityError("confirm_4c4g_target_required")
        if args.output_dir is None or args.output_dir.exists():
            raise AnalysisCapacityError("exclusive_output_directory_required")
        if not args.output_dir.resolve().is_relative_to(ROOT.resolve()):
            raise AnalysisCapacityError("capacity_output_outside_repository")
        if (
            not isinstance(args.execution_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", args.execution_id) is None
        ):
            raise AnalysisCapacityError("execution_id_required")
        if args.external_execution:
            if not all((args.provider, args.region, args.cluster, args.deployed_commit)):
                raise AnalysisCapacityError("external_target_identity_required")
            if COMMIT_PATTERN.fullmatch(args.deployed_commit) is None:
                raise AnalysisCapacityError("deployed_commit_invalid")
            if args.release_manifest is None or not args.resource_ssh_host:
                raise AnalysisCapacityError("external_machine_evidence_required")
            if not args.release_manifest.resolve().is_relative_to(ROOT.resolve()):
                raise AnalysisCapacityError("release_manifest_outside_repository")
            release_identity = _release_identity(
                args.release_manifest, deployed_commit=args.deployed_commit
            )
            image_digests = release_identity["image_digests"]
            if not 5 <= args.resource_sample_seconds <= 60:
                raise AnalysisCapacityError("resource_sample_seconds_invalid")
        else:
            release_identity = None
            image_digests = None
        args.output_dir.mkdir(parents=True)
        settings = FoundationSettings()
        if args.external_execution:
            report, samples, raw_telemetry, resource_snapshots = asyncio.run(
                _run_external_capacity_with_snapshots(
                    plan=plan,
                    settings=settings,
                    execution_id=args.execution_id,
                    ssh_host=args.resource_ssh_host,
                    namespace=args.resource_namespace,
                    interval_seconds=args.resource_sample_seconds,
                )
            )
        else:
            report, samples, raw_telemetry = asyncio.run(
                execute_capacity(plan=plan, settings=settings, execution_id=args.execution_id)
            )
            resource_snapshots = []
        plan_path = args.output_dir / "plan.json"
        samples_path = args.output_dir / "samples.json"
        telemetry_path = args.output_dir / "telemetry.json"
        resources_path = args.output_dir / "resources.json"
        _write_json(plan_path, plan.model_dump(mode="json"))
        _write_json(samples_path, {"samples": samples})
        _write_json(telemetry_path, raw_telemetry)
        _write_json(resources_path, {"snapshots": resource_snapshots})
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if args.external_execution:
            if release_identity is None:
                raise AnalysisCapacityError("release_identity_missing")
            http_requests = report.get("http_requests")
            if isinstance(http_requests, bool) or not isinstance(http_requests, int):
                raise AnalysisCapacityError("http_request_count_invalid")
            resource_evidence = validate_resource_snapshots(
                resource_snapshots,
                plan=plan,
                release=release_identity,
                started_at=datetime.fromisoformat(str(report["started_at"])),
                completed_at=datetime.fromisoformat(str(report["completed_at"])),
                interval_seconds=args.resource_sample_seconds,
                correlation_id=f"analysis-capacity:{args.execution_id}",
                expected_http_requests=http_requests,
            )
            report["status"] = "passed" if report["thresholds_passed"] else "failed"
            report["environment"] = {
                "name": args.cluster,
                "external_execution": True,
                "provider": args.provider,
                "region": args.region,
                "cluster": args.cluster,
                "declared_cpu_cores": plan.target.cpu_cores,
                "declared_memory_bytes": plan.target.memory_bytes,
                "resource_evidence_status": "verified",
                "target_binding_status": "verified",
            }
            report["commit_sha"] = args.deployed_commit
            report["image_digest"] = image_digests
            report["release_identity"] = release_identity
            report["resource_evidence"] = resource_evidence
        else:
            report["commit_sha"] = revision
            report["image_digest"] = None
            report["blocking_reason"] = (
                "The workload ran on an operator-confirmed local 4C4G Docker target, not an "
                "independently reviewed production-like environment."
            )
            report["prerequisites"] = [
                "Repeat on an isolated production-like 4C4G target with immutable images.",
                "Independently verify runtime CPU and memory limits.",
                (
                    "Complete workload-owner review of saturation, queueing, "
                    "cancellation, and recovery."
                ),
            ]
        report.update(
            {
                "evidence_id": f"analysis-capacity-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                "milestone": "A5",
                "requirement_ids": ["A5-4C4G", "R5", "R10"],
                "executor_commit_sha": revision,
                "operator": args.operator,
                "command_or_procedure": [
                    "run_analysis_capacity.py",
                    plan.profile,
                    *PHASES,
                ],
                "owner": "document-analysis",
                "artifacts": [
                    _artifact(ROOT, plan_path, "analysis-capacity-plan"),
                    _artifact(ROOT, samples_path, "analysis-capacity-samples"),
                    _artifact(ROOT, telemetry_path, "analysis-capacity-telemetry"),
                    _artifact(ROOT, resources_path, "analysis-capacity-resources"),
                    *(
                        [
                            _artifact(
                                ROOT,
                                args.release_manifest,
                                "analysis-capacity-release-manifest",
                            )
                        ]
                        if args.external_execution
                        else []
                    ),
                ],
            }
        )
        validate_evidence(report, root=ROOT)
        report_path = args.output_dir / "report.json"
        _write_json(report_path, report)
    except (
        AnalysisCapacityError,
        AnalysisEvaluationError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({"status": report["status"], "report": str(report_path)}))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
