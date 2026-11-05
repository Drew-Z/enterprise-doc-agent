"""Validate or execute a fixed-versus-Agent analysis holdout through the product API.

The evaluator never calls a model provider directly. Live calls are admitted by the
analysis API and executed by ``AnalysisExecutionService``, so the product quota,
``ProviderCallService`` ledger, two-slot limit, and durable retry policy remain the
only dispatch authority. The reference file is bound for later scoring but is never
opened by this module or included in a model input.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select, text

from enterprise_doc_core.analysis.execution_models import (
    AnalysisAction,
    AnalysisRun,
    AnalysisUnit,
)
from enterprise_doc_core.analysis.execution_schemas import MatrixView, RunView
from enterprise_doc_core.analysis.gateway import freeze_policy
from enterprise_doc_core.analysis.schemas import SnapshotView, canonical_hash
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.billing.product_models import (
    ProductUsageEvent,
    ProductUsageReservation,
)
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import FoundationSettings, ModelProvider
from enterprise_doc_core.db import create_database_engine, create_session_factory, read_only_session

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_RUN_STATES = {
    "waiting_input",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "expired",
}
TOKEN_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class AnalysisEvaluationError(ValueError):
    """A stable evaluator-side rejection that is safe to record."""


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExpectedPolicy(PlanModel):
    route_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    model_revision: str | None = Field(default=None, max_length=128)
    streaming: bool
    reasoning_effort: str | None = Field(default=None, max_length=32)
    max_dispatches_per_unit: Literal[2] = 2


class AnalysisHoldoutPlan(PlanModel):
    schema_version: Literal[1]
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    pack_manifest: str = Field(min_length=1, max_length=500)
    pack_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_bytes: int = Field(ge=1, le=2 * 1024**2, strict=True)
    base_url: str = Field(min_length=1, max_length=500)
    token_env: str = Field(default="ANALYSIS_HOLDOUT_TOKEN")
    project_id: UUID
    snapshot_id: UUID
    modes: tuple[Literal["fixed", "agent"], ...] = ("fixed", "agent")
    expected_policy: ExpectedPolicy
    max_live_dispatches: int = Field(ge=1, le=200, strict=True)
    poll_seconds: float = Field(default=0.5, ge=0.05, le=10)
    run_timeout_seconds: float = Field(default=3600, gt=0, le=21600)

    @model_validator(mode="after")
    def coherent(self) -> AnalysisHoldoutPlan:
        if self.modes != ("fixed", "agent"):
            raise ValueError("modes must be exactly fixed then agent")
        if TOKEN_ENV_PATTERN.fullmatch(self.token_env) is None:
            raise ValueError("token_env must be an uppercase environment variable name")
        _validated_origin(self.base_url)
        return self


@dataclass(frozen=True)
class LoadedFrozenPack:
    pack_id: str
    split: str
    manifest_path: Path
    manifest_sha256: str
    source_files: tuple[Path, ...]
    source_records: tuple[dict[str, object], ...]
    row_keys: tuple[tuple[str, str], ...]
    denominator: int
    reference_path: Path
    reference_sha256: str
    reference_bytes: int

    def model_input_manifest(self) -> dict[str, object]:
        """Return only identities that may be associated with model execution."""
        return {
            "pack_id": self.pack_id,
            "split": self.split,
            "manifest_sha256": self.manifest_sha256,
            "denominator": self.denominator,
            "sources": [
                {
                    "filename": str(item["filename"]),
                    "object_id": item["object_id"],
                    "sha256": str(item["sha256"]),
                    "bytes": int(item["bytes"]),
                }
                for item in self.source_records
            ],
            "row_keys": [
                {"object_id": object_id, "field_id": field_id}
                for object_id, field_id in self.row_keys
            ],
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_repo_path(root: Path, value: str, *, label: str) -> Path:
    if "\\" in value:
        raise AnalysisEvaluationError(f"{label}_path_invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise AnalysisEvaluationError(f"{label}_path_invalid")
    candidate = (root.resolve() / Path(*pure.parts)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise AnalysisEvaluationError(f"{label}_path_invalid")
    return candidate


def _validated_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("base_url_invalid") from None
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
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
        raise ValueError("base_url_invalid")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def load_plan(path: Path) -> AnalysisHoldoutPlan:
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise AnalysisEvaluationError("evaluation_plan_invalid")
    try:
        return AnalysisHoldoutPlan.model_validate_json(path.read_bytes())
    except ValidationError:
        raise AnalysisEvaluationError("evaluation_plan_invalid") from None


def load_frozen_pack(plan: AnalysisHoldoutPlan, *, root: Path = ROOT) -> LoadedFrozenPack:
    manifest_path = _safe_repo_path(root, plan.pack_manifest, label="pack_manifest")
    if not manifest_path.is_file() or manifest_path.stat().st_size > 256 * 1024:
        raise AnalysisEvaluationError("pack_manifest_invalid")
    raw = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if manifest_sha256 != plan.pack_manifest_sha256:
        raise AnalysisEvaluationError("pack_manifest_integrity_failed")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        raise AnalysisEvaluationError("pack_manifest_invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "analysis.public-pack.v2"
    ):
        raise AnalysisEvaluationError("pack_manifest_invalid")
    objects = manifest.get("objects")
    fields = manifest.get("fields")
    sources = manifest.get("sources")
    common = manifest.get("common_requirements")
    if (
        not isinstance(objects, list)
        or not isinstance(fields, list)
        or not isinstance(sources, list)
        or not isinstance(common, dict)
        or not 1 <= len(objects) <= 3
        or not 1 <= len(fields) <= 10
    ):
        raise AnalysisEvaluationError("pack_manifest_invalid")
    object_ids = [item.get("id") for item in objects if isinstance(item, dict)]
    field_ids = [item.get("id") for item in fields if isinstance(item, dict)]
    if (
        len(object_ids) != len(objects)
        or len(field_ids) != len(fields)
        or len(set(object_ids)) != len(object_ids)
        or len(set(field_ids)) != len(field_ids)
        or not all(isinstance(item, str) and item for item in (*object_ids, *field_ids))
    ):
        raise AnalysisEvaluationError("pack_manifest_invalid")

    records: list[dict[str, object]] = []
    paths: list[Path] = []
    for item in sources:
        if not isinstance(item, dict) or item.get("object_id") not in object_ids:
            raise AnalysisEvaluationError("pack_source_invalid")
        relative = item.get("file")
        if not isinstance(relative, str) or not relative.startswith("upload/"):
            raise AnalysisEvaluationError("pack_source_invalid")
        source = _safe_repo_path(manifest_path.parent, relative, label="pack_source")
        _verify_file_identity(source, item)
        paths.append(source)
        records.append(
            {
                "filename": source.name,
                "object_id": item["object_id"],
                "sha256": item["sha256"],
                "bytes": item["bytes"],
            }
        )
    common_relative = common.get("file")
    if not isinstance(common_relative, str) or not common_relative.startswith("upload/"):
        raise AnalysisEvaluationError("pack_source_invalid")
    common_path = _safe_repo_path(manifest_path.parent, common_relative, label="pack_source")
    _verify_file_identity(common_path, common)
    paths.append(common_path)
    records.append(
        {
            "filename": common_path.name,
            "object_id": None,
            "sha256": common["sha256"],
            "bytes": common["bytes"],
        }
    )

    reference_relative = manifest.get("reference")
    if not isinstance(reference_relative, str) or reference_relative.startswith("upload/"):
        raise AnalysisEvaluationError("pack_reference_invalid")
    reference_path = _safe_repo_path(
        manifest_path.parent, reference_relative, label="pack_reference"
    )
    if not reference_path.is_file() or reference_path.is_relative_to(
        manifest_path.parent / "upload"
    ):
        raise AnalysisEvaluationError("pack_reference_invalid")
    row_keys = tuple((str(obj), str(field)) for obj in object_ids for field in field_ids)
    return LoadedFrozenPack(
        pack_id=str(manifest.get("id", "")),
        split=str(manifest.get("split", "")),
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        source_files=tuple(paths),
        source_records=tuple(records),
        row_keys=row_keys,
        denominator=len(row_keys),
        reference_path=reference_path,
        reference_sha256=plan.reference_sha256,
        reference_bytes=plan.reference_bytes,
    )


def _verify_file_identity(path: Path, declared: dict[str, object]) -> None:
    size = declared.get("bytes")
    digest = declared.get("sha256")
    if (
        not path.is_file()
        or isinstance(size, bool)
        or not isinstance(size, int)
        or path.stat().st_size != size
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or _sha256(path) != digest
    ):
        raise AnalysisEvaluationError("pack_source_integrity_failed")


def build_configuration_preflight(
    plan: AnalysisHoldoutPlan,
    pack: LoadedFrozenPack,
    settings: FoundationSettings,
    *,
    token_present: bool,
) -> dict[str, object]:
    blocking: list[str] = []
    if not token_present:
        blocking.append("analysis_auth_token_missing")
    if not settings.analysis.execution_enabled:
        blocking.append("analysis_execution_disabled")
    if not settings.upload.structured_documents_enabled:
        blocking.append("structured_uploads_disabled")
    observed_policy: dict[str, object] | None = None
    if settings.model.provider is not ModelProvider.OPENAI_COMPATIBLE:
        blocking.append("analysis_model_route_unconfigured")
    else:
        try:
            policy = freeze_policy(settings.model, settings.analysis)
        except AnalysisError:
            blocking.append("analysis_model_route_unconfigured")
        else:
            observed_policy = {
                "route_id": policy.route_id,
                "model": policy.model,
                "model_revision": policy.model_revision,
                "streaming": policy.streaming,
                "reasoning_effort": policy.reasoning_effort,
                "max_dispatches_per_unit": policy.max_dispatches,
                "schema_version": policy.schema_version,
                "prompt_version": policy.prompt_version,
                "channel_hash": policy.channel_hash,
                "policy_hash": canonical_hash(policy),
            }
            expected = plan.expected_policy.model_dump(mode="json")
            if any(observed_policy[key] != value for key, value in expected.items()):
                blocking.append("analysis_expected_policy_mismatch")
    effective_daily_limit = min(200, settings.provider_usage.daily_call_limit)
    if plan.max_live_dispatches > effective_daily_limit:
        blocking.append("analysis_plan_exceeds_daily_budget")
    if settings.provider_usage.agent_call_limit < 2:
        blocking.append("analysis_two_slot_budget_unavailable")
    return {
        "schema_version": 1,
        "kind": "analysis_holdout_preflight",
        "status": "ready" if not blocking else "blocked",
        "suite_id": plan.suite_id,
        "pack_id": pack.pack_id,
        "pack_manifest_sha256": pack.manifest_sha256,
        "row_denominator": pack.denominator,
        "planned_denominator": pack.denominator * len(plan.modes),
        "planned_max_dispatches": plan.max_live_dispatches,
        "effective_daily_dispatch_limit": effective_daily_limit,
        "expected_policy": plan.expected_policy.model_dump(mode="json"),
        "observed_policy": observed_policy,
        "blocking_codes": sorted(set(blocking)),
        "network_requests": 0,
        "external_model_calls": 0,
        "reference_opened": False,
        "reference_in_model_input": False,
        "reference_identity": {
            "sha256": pack.reference_sha256,
            "bytes": pack.reference_bytes,
        },
        "limitations": [
            "This is a local configuration and frozen-material preflight only.",
            "No API, model, embedding, or scoring request was made.",
            "The target API and database budgets are rechecked before live execution.",
        ],
        "recorded_at": datetime.now(UTC).isoformat(),
    }


class AnalysisApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.origin = _validated_origin(base_url)
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.requests = 0

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.origin,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        target = client.base_url.join(path)
        expected = httpx.URL(self.origin)
        if target.userinfo or (target.scheme, target.host, target.port) != (
            expected.scheme,
            expected.host,
            expected.port,
        ):
            raise AnalysisEvaluationError("analysis_api_origin_mismatch")
        self.requests += 1
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await client.request(method, path, json=payload, headers=headers)
        except (httpx.HTTPError, TimeoutError):
            raise AnalysisEvaluationError("analysis_api_unavailable") from None
        if len(response.content) > 8 * 1024 * 1024:
            raise AnalysisEvaluationError("analysis_api_response_too_large")
        if response.status_code not in accepted:
            code = "analysis_api_rejected"
            try:
                candidate = response.json().get("error", {}).get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_]{1,100}", candidate):
                    code = candidate
            except (ValueError, AttributeError, TypeError):
                pass
            raise AnalysisEvaluationError(code)
        return response


def _decode(model: type[BaseModel], response: httpx.Response) -> BaseModel:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise AnalysisEvaluationError("analysis_api_contract_invalid") from None


def _snapshot_matches_pack(snapshot: SnapshotView, pack: LoadedFrozenPack) -> bool:
    actual_sources = sorted(
        (
            item.filename,
            item.object_id,
            item.source_sha256,
        )
        for item in snapshot.payload.sources
    )
    expected_sources = sorted(
        (str(item["filename"]), item["object_id"], str(item["sha256"]))
        for item in pack.source_records
    )
    return (
        actual_sources == expected_sources
        and tuple(item.id for item in snapshot.payload.spec.objects)
        == tuple(dict.fromkeys(object_id for object_id, _ in pack.row_keys))
        and tuple(item.id for item in snapshot.payload.spec.fields)
        == tuple(dict.fromkeys(field_id for _, field_id in pack.row_keys))
    )


async def live_preflight(
    *,
    api: AnalysisApiClient,
    plan: AnalysisHoldoutPlan,
    pack: LoadedFrozenPack,
) -> tuple[dict[str, object], UUID]:
    async with api.client() as client:
        capabilities = (await api.request(client, "GET", "/api/analysis/capabilities")).json()
        if not all(
            capabilities.get(key) is True
            for key in ("admissionEnabled", "executionEnabled", "structuredUploadsEnabled")
        ):
            raise AnalysisEvaluationError("analysis_target_capabilities_disabled")
        snapshots_response = await api.request(
            client, "GET", f"/api/analysis/projects/{plan.project_id}/snapshots"
        )
        try:
            snapshots = tuple(
                SnapshotView.model_validate(item) for item in snapshots_response.json()
            )
        except (ValueError, TypeError, ValidationError):
            raise AnalysisEvaluationError("analysis_api_contract_invalid") from None
        matches = [item for item in snapshots if item.id == plan.snapshot_id]
        if len(matches) != 1 or not _snapshot_matches_pack(matches[0], pack):
            raise AnalysisEvaluationError("analysis_snapshot_pack_mismatch")
        usage = (await api.request(client, "GET", "/api/tenant-usage")).json()
    tenant_id = _uuid_field(usage, "tenantId")
    quotas = usage.get("productQuotas")
    if not isinstance(quotas, list):
        raise AnalysisEvaluationError("analysis_usage_contract_invalid")
    cells = [
        item for item in quotas if isinstance(item, dict) and item.get("metric") == "analysis_cell"
    ]
    required_cells = pack.denominator * len(plan.modes)
    if len(cells) != 1 or not isinstance(cells[0].get("remaining"), int):
        raise AnalysisEvaluationError("analysis_cell_quota_unavailable")
    if int(cells[0]["remaining"]) < required_cells:
        raise AnalysisEvaluationError("analysis_cell_quota_insufficient")
    return (
        {
            "status": "ready",
            "tenant_id": str(tenant_id),
            "snapshot_id": str(matches[0].id),
            "source_manifest_hash": matches[0].source_manifest_hash,
            "analysis_cells_required": required_cells,
            "analysis_cells_remaining": cells[0]["remaining"],
            "network_requests": api.requests,
        },
        tenant_id,
    )


def _uuid_field(payload: object, key: str) -> UUID:
    if not isinstance(payload, dict):
        raise AnalysisEvaluationError("analysis_api_contract_invalid")
    try:
        return UUID(str(payload[key]))
    except (KeyError, ValueError, TypeError):
        raise AnalysisEvaluationError("analysis_api_contract_invalid") from None


async def shared_budget_snapshot(
    *, settings: FoundationSettings, required_dispatches: int
) -> dict[str, int]:
    engine = create_database_engine(settings.database)
    sessions = create_session_factory(engine)
    try:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with read_only_session(sessions) as session:
            presales = int(
                await session.scalar(
                    text(
                        "SELECT COALESCE(SUM(dispatched), 0) FROM presales_dispatch_days "
                        "WHERE day = :day"
                    ),
                    {"day": start.date()},
                )
                or 0
            )
            analysis = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ProviderDispatch)
                    .where(
                        ProviderDispatch.kind == "agent",
                        ProviderDispatch.started_at >= start,
                        ProviderDispatch.started_at < start + timedelta(days=1),
                    )
                )
                or 0
            )
        limit = 200
        remaining = max(0, limit - presales - analysis)
        if remaining < required_dispatches:
            raise AnalysisEvaluationError("provider_generation_budget_insufficient")
        return {
            "limit": limit,
            "presales_dispatches": presales,
            "provider_agent_dispatches": analysis,
            "remaining": remaining,
            "required": required_dispatches,
        }
    finally:
        await engine.dispose()


async def tenant_budget_snapshot(
    *, settings: FoundationSettings, tenant_id: UUID, required_dispatches: int
) -> dict[str, int]:
    engine = create_database_engine(settings.database)
    sessions = create_session_factory(engine)
    try:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with read_only_session(sessions) as session:
            dispatched = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ProviderDispatch)
                    .where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.started_at >= start,
                        ProviderDispatch.started_at < start + timedelta(days=1),
                    )
                )
                or 0
            )
        limit = settings.provider_usage.daily_call_limit
        remaining = max(0, limit - dispatched)
        if remaining < required_dispatches:
            raise AnalysisEvaluationError("provider_daily_budget_insufficient")
        return {
            "limit": limit,
            "dispatches": dispatched,
            "remaining": remaining,
            "required": required_dispatches,
        }
    finally:
        await engine.dispose()


async def execute_arm(
    *,
    api: AnalysisApiClient,
    plan: AnalysisHoldoutPlan,
    pack: LoadedFrozenPack,
    mode: Literal["fixed", "agent"],
    execution_id: str,
) -> dict[str, object]:
    key = f"analysis-eval-{plan.suite_id}-{execution_id}-{mode}"
    if len(key) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise AnalysisEvaluationError("analysis_execution_id_invalid")
    started = time.perf_counter()
    async with api.client() as client:
        created = _decode(
            RunView,
            await api.request(
                client,
                "POST",
                f"/api/analysis/projects/{plan.project_id}/runs",
                payload={"snapshotId": str(plan.snapshot_id), "executionMode": mode},
                headers={"Idempotency-Key": key},
                accepted=(202,),
            ),
        )
        assert isinstance(created, RunView)
        if (
            created.project_id != plan.project_id
            or created.snapshot_id != plan.snapshot_id
            or created.execution_mode != mode
            or created.total_units != pack.denominator
        ):
            raise AnalysisEvaluationError("analysis_run_identity_mismatch")
        deadline = time.monotonic() + plan.run_timeout_seconds
        matrix: MatrixView | None = None
        while time.monotonic() < deadline:
            decoded = _decode(
                MatrixView,
                await api.request(client, "GET", f"/api/analysis/runs/{created.id}/matrix"),
            )
            assert isinstance(decoded, MatrixView)
            matrix = decoded
            if matrix.run.status in TERMINAL_RUN_STATES:
                break
            await asyncio.sleep(plan.poll_seconds)
        if matrix is None or matrix.run.status not in TERMINAL_RUN_STATES:
            try:
                await api.request(
                    client, "POST", f"/api/analysis/runs/{created.id}/cancel", accepted=(200,)
                )
            except AnalysisEvaluationError:
                pass
            raise AnalysisEvaluationError("analysis_run_timeout")
    actual_keys = {(item.object_id, item.field_id) for item in matrix.units}
    if actual_keys != set(pack.row_keys) or len(matrix.units) != pack.denominator:
        raise AnalysisEvaluationError("analysis_matrix_denominator_mismatch")
    rows = [
        {
            "object_id": item.object_id,
            "field_id": item.field_id,
            "status": item.status,
            "dispatches": item.dispatches,
            "error_code": item.error_code,
            "result": (
                item.result.model_dump(mode="json", by_alias=False)
                if item.result is not None
                else None
            ),
            "search": (
                item.search.model_dump(mode="json", by_alias=False)
                if item.search is not None
                else None
            ),
            "input_required": item.input_request is not None,
        }
        for item in matrix.units
    ]
    return {
        "mode": mode,
        "status": matrix.run.status,
        "run_id": str(matrix.run.id),
        "latency_ms": (time.perf_counter() - started) * 1000,
        "dispatches": matrix.run.dispatches,
        "rows": rows,
    }


async def observe_run_ledger(
    *,
    settings: FoundationSettings,
    tenant_id: UUID,
    run_id: UUID,
    expected: ExpectedPolicy,
) -> dict[str, object]:
    engine = create_database_engine(settings.database)
    sessions = create_session_factory(engine)
    try:
        async with read_only_session(sessions) as session:
            run = await session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.id == run_id, AnalysisRun.tenant_id == tenant_id
                )
            )
            if run is None:
                raise AnalysisEvaluationError("analysis_run_ledger_missing")
            policy = dict(run.policy)
            for key, value in expected.model_dump(mode="json").items():
                policy_key = "max_dispatches" if key == "max_dispatches_per_unit" else key
                if policy.get(policy_key) != value:
                    raise AnalysisEvaluationError("analysis_run_policy_mismatch")
            units = tuple(
                (
                    await session.scalars(select(AnalysisUnit).where(AnalysisUnit.run_id == run_id))
                ).all()
            )
            actions = tuple(
                (
                    await session.scalars(
                        select(AnalysisAction)
                        .join(AnalysisUnit, AnalysisUnit.id == AnalysisAction.unit_id)
                        .where(AnalysisUnit.run_id == run_id)
                        .order_by(AnalysisUnit.ordinal, AnalysisAction.slot)
                    )
                ).all()
            )
            dispatch_ids = [item.provider_dispatch_id for item in actions]
            dispatches = (
                tuple(
                    (
                        await session.scalars(
                            select(ProviderDispatch)
                            .where(ProviderDispatch.id.in_(dispatch_ids))
                            .order_by(ProviderDispatch.started_at, ProviderDispatch.id)
                        )
                    ).all()
                )
                if dispatch_ids
                else ()
            )
            reservations = tuple(
                (
                    await session.scalars(
                        select(ProductUsageReservation).where(
                            ProductUsageReservation.tenant_id == tenant_id,
                            ProductUsageReservation.operation_id.in_([item.id for item in units]),
                            ProductUsageReservation.metric == "analysis_cell",
                        )
                    )
                ).all()
            )
            events = tuple(
                (
                    await session.scalars(
                        select(ProductUsageEvent).where(
                            ProductUsageEvent.reservation_id.in_([item.id for item in reservations])
                        )
                    )
                ).all()
            )
        if len(actions) != len(dispatches):
            raise AnalysisEvaluationError("analysis_dispatch_ledger_mismatch")
        if any(
            item.model != expected.model or item.channel_hash != policy.get("channel_hash")
            for item in dispatches
        ):
            raise AnalysisEvaluationError("analysis_dispatch_identity_mismatch")
        known_costs = [item.estimated_cost for item in dispatches]
        cost: Decimal | None = (
            sum((value for value in known_costs if value is not None), Decimal(0))
            if dispatches and all(value is not None for value in known_costs)
            else None
        )
        known_tokens = [item.total_tokens for item in dispatches if item.total_tokens is not None]
        return {
            "policy": {
                "schema_version": policy.get("schema_version"),
                "prompt_version": policy.get("prompt_version"),
                "route_id": policy.get("route_id"),
                "model": policy.get("model"),
                "model_revision": policy.get("model_revision"),
                "streaming": policy.get("streaming"),
                "reasoning_effort": policy.get("reasoning_effort"),
                "max_dispatches_per_unit": policy.get("max_dispatches"),
                "channel_hash": policy.get("channel_hash"),
                "policy_hash": run.policy_hash,
            },
            "usage": {
                "dispatches": len(dispatches),
                "states": {
                    state: sum(item.state == state for item in dispatches)
                    for state in (
                        "dispatched",
                        "responded",
                        "http_error",
                        "timeout",
                        "transport_error",
                        "cancelled",
                        "unknown",
                    )
                },
                "known_total_tokens": sum(known_tokens),
                "unknown_token_dispatches": len(dispatches) - len(known_tokens),
                "estimated_cost": str(cost) if cost is not None else None,
                "currency": (
                    next(iter({item.currency for item in dispatches}))
                    if dispatches and len({item.currency for item in dispatches}) == 1
                    else None
                ),
            },
            "settlement": {
                "reservations": len(reservations),
                "reserved": sum(item.state == "reserved" for item in reservations),
                "consumed": sum(item.state == "consumed" for item in reservations),
                "released": sum(item.state == "released" for item in reservations),
                "consume_events": sum(item.event_type == "consume" for item in events),
                "release_events": sum(item.event_type == "release" for item in events),
            },
        }
    finally:
        await engine.dispose()


def _not_run_rows(pack: LoadedFrozenPack, reason: str) -> list[dict[str, object]]:
    return [
        {
            "object_id": object_id,
            "field_id": field_id,
            "status": "not_run",
            "dispatches": 0,
            "error_code": reason,
            "result": None,
        }
        for object_id, field_id in pack.row_keys
    ]


def build_execution_report(
    *,
    plan: AnalysisHoldoutPlan,
    pack: LoadedFrozenPack,
    arms: dict[str, dict[str, object]],
    stopped_reason: str | None = None,
    preflight: dict[str, object] | None = None,
) -> dict[str, object]:
    normalized: dict[str, dict[str, object]] = {}
    observed = 0
    for mode in plan.modes:
        supplied = arms.get(mode)
        if supplied is None:
            normalized[mode] = {
                "mode": mode,
                "status": "not_run",
                "run_id": None,
                "latency_ms": None,
                "policy": None,
                "usage": {
                    "dispatches": 0,
                    "known_total_tokens": 0,
                    "unknown_token_dispatches": 0,
                    "estimated_cost": None,
                },
                "rows": _not_run_rows(pack, stopped_reason or "upstream_not_completed"),
            }
            continue
        rows = supplied.get("rows")
        if not isinstance(rows, list):
            raise AnalysisEvaluationError("analysis_arm_rows_invalid")
        keyed = {
            (str(row.get("object_id")), str(row.get("field_id"))): row
            for row in rows
            if isinstance(row, dict)
        }
        complete_rows = []
        for key in pack.row_keys:
            value = keyed.get(key)
            if value is None:
                value = _not_run_rows(
                    LoadedFrozenPack(
                        pack.pack_id,
                        pack.split,
                        pack.manifest_path,
                        pack.manifest_sha256,
                        pack.source_files,
                        pack.source_records,
                        (key,),
                        1,
                        pack.reference_path,
                        pack.reference_sha256,
                        pack.reference_bytes,
                    ),
                    "row_missing_from_execution",
                )[0]
            else:
                observed += int(value.get("status") != "not_run")
            complete_rows.append(value)
        normalized[mode] = {**supplied, "rows": complete_rows}
    planned = pack.denominator * len(plan.modes)
    complete = observed == planned and all(
        normalized[mode]["status"] in TERMINAL_RUN_STATES for mode in plan.modes
    )
    return {
        "schema_version": "analysis.holdout-execution.v1",
        "suite_id": plan.suite_id,
        "pack_id": pack.pack_id,
        "pack_manifest_sha256": pack.manifest_sha256,
        "status": "completed" if complete else "stopped",
        "planned_denominator": planned,
        "observed_rows": observed,
        "not_run_rows": planned - observed,
        "stopped_reason": stopped_reason,
        "model_inputs": pack.model_input_manifest(),
        "reference_boundary": {
            "path": pack.reference_path.relative_to(ROOT).as_posix()
            if pack.reference_path.is_relative_to(ROOT)
            else pack.reference_path.as_posix(),
            "opened_by_executor": False,
            "included_in_model_input": False,
            "score_separately": True,
            "sha256": pack.reference_sha256,
            "bytes": pack.reference_bytes,
        },
        "preflight": preflight,
        "arms": normalized,
        "limitations": [
            "Reference-based scoring is deliberately absent from this execution report.",
            "Unknown token usage or cost remains null/unknown; it is never treated as zero.",
            "A single provider run does not establish repeatability or customer value.",
        ],
        "completed_at": datetime.now(UTC).isoformat(),
    }


async def execute_evaluation(
    *,
    plan: AnalysisHoldoutPlan,
    pack: LoadedFrozenPack,
    settings: FoundationSettings,
    token: str,
    execution_id: str,
) -> dict[str, object]:
    api = AnalysisApiClient(base_url=plan.base_url, token=token)
    live, tenant_id = await live_preflight(api=api, plan=plan, pack=pack)
    budget = await shared_budget_snapshot(
        settings=settings,
        required_dispatches=plan.max_live_dispatches,
    )
    tenant_budget = await tenant_budget_snapshot(
        settings=settings,
        tenant_id=tenant_id,
        required_dispatches=plan.max_live_dispatches,
    )
    arms: dict[str, dict[str, object]] = {}
    stopped_reason: str | None = None
    for mode in plan.modes:
        if stopped_reason is not None:
            break
        try:
            arm = await execute_arm(
                api=api,
                plan=plan,
                pack=pack,
                mode=mode,
                execution_id=execution_id,
            )
            ledger = await observe_run_ledger(
                settings=settings,
                tenant_id=tenant_id,
                run_id=UUID(str(arm["run_id"])),
                expected=plan.expected_policy,
            )
            arm.update(ledger)
            arms[mode] = arm
            states = ledger["usage"]["states"]
            if states["dispatched"] or states["unknown"] or states["timeout"]:
                stopped_reason = "analysis_unresolved_dispatch_observed"
        except AnalysisEvaluationError as error:
            stopped_reason = str(error)
    return build_execution_report(
        plan=plan,
        pack=pack,
        arms=arms,
        stopped_reason=stopped_reason,
        preflight={
            "target": live,
            "shared_generation_budget": budget,
            "tenant_provider_budget": tenant_budget,
            "network_requests": api.requests,
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--execution-id")
    args = parser.parse_args()
    try:
        plan = load_plan(args.plan)
        pack = load_frozen_pack(plan)
        settings = FoundationSettings()
        token = os.environ.get(plan.token_env, "")
        preflight = build_configuration_preflight(plan, pack, settings, token_present=bool(token))
        if not args.execute or preflight["status"] != "ready":
            _write_json(args.output, preflight)
            print(json.dumps({"status": preflight["status"], "output": str(args.output)}))
            if preflight["status"] != "ready":
                raise SystemExit(2)
            return
        if (
            not isinstance(args.execution_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", args.execution_id) is None
        ):
            raise AnalysisEvaluationError("analysis_execution_id_required")
        report = asyncio.run(
            execute_evaluation(
                plan=plan,
                pack=pack,
                settings=settings,
                token=token,
                execution_id=args.execution_id,
            )
        )
        _write_json(args.output, report)
    except (AnalysisEvaluationError, OSError, ValueError) as error:
        if isinstance(error, SystemExit):
            raise
        parser.exit(2, f"{error}\n")
    print(json.dumps({"status": report["status"], "output": str(args.output)}))
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
