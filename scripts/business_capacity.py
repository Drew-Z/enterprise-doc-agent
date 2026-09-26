"""Bounded local business samples, separate from production capacity approval."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io as textio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from enterprise_doc_api.documents.router import DocumentInventoryItemResponse
from enterprise_doc_api.uploads.router import (
    PresignUploadPartResponse,
    UploadSessionCompleteResponse,
    UploadSessionCreateResponse,
)
from enterprise_doc_core.evaluation import build_percentile_summary
from enterprise_doc_core.presales.schemas import PacketView, ReviewInput, RowView

BOUNDARIES = ("upload", "ingestion", "retrieval", "generation_recovery")
PHASES = ("ramp", "steady_state", "burst", "recovery")
PositiveSeconds = Annotated[float, Field(gt=0, le=600, allow_inf_nan=False, strict=True)]
MEDIA_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def loopback_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_loopback_origin") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
    ):
        raise ValueError("invalid_loopback_origin")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BusinessPhase(PlanModel):
    name: Literal["ramp", "steady_state", "burst", "recovery"]
    tasks: int = Field(ge=1, le=80, strict=True)
    concurrency: int = Field(ge=1, le=4, strict=True)


class BusinessCase(PlanModel):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    path: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=1024 * 1024, strict=True)
    media_type: str
    query: str = Field(min_length=1, max_length=2000)
    excerpt: str = Field(min_length=1, max_length=600)
    expected_terminal: Literal["drafted", "failed"] = "drafted"
    fault_label: Literal["none", "primary_failure", "both_routes_failure"] = "none"
    min_provider_calls: int = Field(default=1, ge=1, le=2, strict=True)
    max_provider_calls: int = Field(default=2, ge=1, le=2, strict=True)

    @model_validator(mode="after")
    def check_case(self) -> Self:
        if MEDIA_TYPES.get(Path(self.path).suffix.lower()) != self.media_type:
            raise ValueError("fixture_media_type")
        if self.min_provider_calls > self.max_provider_calls:
            raise ValueError("provider_call_bounds")
        if (self.expected_terminal == "failed") != (self.fault_label == "both_routes_failure"):
            raise ValueError("expected_fault_mismatch")
        if not self.query.strip() or not self.excerpt.strip():
            raise ValueError("empty_business_assertion")
        return self


class BusinessPlan(PlanModel):
    schema_version: Literal[1]
    base_url: str
    object_origins: tuple[str, ...] = Field(min_length=1, max_length=4)
    token_env: str = Field(default="ENTERPRISE_DOC_LOAD_TOKEN", pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    repetitions: int = Field(ge=1, le=2, strict=True)
    phases: tuple[BusinessPhase, ...] = Field(min_length=4, max_length=4)
    cases: tuple[BusinessCase, ...] = Field(min_length=1, max_length=8)
    request_timeout_seconds: PositiveSeconds = 10
    upload_timeout_seconds: PositiveSeconds = 30
    ingestion_timeout_seconds: PositiveSeconds = 60
    retrieval_timeout_seconds: PositiveSeconds = 30
    generation_timeout_seconds: PositiveSeconds = 180
    task_timeout_seconds: PositiveSeconds = 360
    poll_seconds: float = Field(default=0.25, ge=0.01, le=5, strict=True)
    max_http_requests: int = Field(default=10000, ge=1, le=20000, strict=True)
    max_run_seconds: float = Field(default=1800, gt=0, le=7200, strict=True)
    upload_target_ms: float = Field(default=5000, gt=0, strict=True)
    ingestion_target_ms: float = Field(default=30000, gt=0, strict=True)
    retrieval_target_ms: float = Field(default=2000, gt=0, strict=True)
    accept_target_ms: float = Field(default=250, gt=0, strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("integer_schema_version_required")
        return value

    @property
    def total_tasks(self) -> int:
        return self.repetitions * sum(phase.tasks for phase in self.phases)

    @model_validator(mode="after")
    def check_plan(self) -> Self:
        loopback_origin(self.base_url)
        for origin in self.object_origins:
            loopback_origin(origin)
        if tuple(phase.name for phase in self.phases) != PHASES:
            raise ValueError("business_phase_order")
        if self.total_tasks > 160:
            raise ValueError("business_task_budget")
        if len({case.key for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case")
        return self


@dataclass(frozen=True)
class LoadedCase:
    spec: BusinessCase
    content: bytes


@dataclass(frozen=True)
class LoadedBusinessPlan:
    plan: BusinessPlan
    sha256: str
    cases: tuple[LoadedCase, ...]


def load_business_plan(path: Path) -> LoadedBusinessPlan:
    if path.stat().st_size > 128 * 1024:
        raise ValueError("plan_too_large")
    raw = path.read_bytes()
    try:
        plan = BusinessPlan.model_validate_json(raw)
    except ValidationError:
        raise ValueError("invalid_business_plan") from None
    cases = []
    for spec in plan.cases:
        source = (path.parent / spec.path).resolve()
        if not source.is_relative_to(path.parent.resolve()):
            raise ValueError("fixture_path")
        if source.stat().st_size != spec.size_bytes:
            raise ValueError("fixture_integrity")
        with source.open("rb") as stream:
            content = stream.read(spec.size_bytes + 1)
        if len(content) != spec.size_bytes or hashlib.sha256(content).hexdigest() != spec.sha256:
            raise ValueError("fixture_integrity")
        cases.append(LoadedCase(spec, content))
    return LoadedBusinessPlan(plan, hashlib.sha256(raw).hexdigest(), tuple(cases))


class BusinessFailure(ValueError):
    """Only locally selected diagnostic codes, never upstream exception text."""


class BusinessIO:
    def __init__(
        self,
        plan: BusinessPlan,
        token: str,
        *,
        api_transport: httpx.AsyncBaseTransport | None = None,
        object_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.plan, self.token = plan, token
        self.api_transport, self.object_transport = api_transport, object_transport
        self.requests = 0
        self.exhausted = False

    def client(self, *, objects: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="" if objects else loopback_origin(self.plan.base_url),
            transport=self.object_transport if objects else self.api_transport,
            headers={} if objects else {"Authorization": f"Bearer {self.token}"},
            timeout=self.plan.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        accepted: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        if self.requests >= self.plan.max_http_requests:
            self.exhausted = True
            raise BusinessFailure("http_budget_exhausted")
        self.requests += 1
        try:
            async with asyncio.timeout(self.plan.request_timeout_seconds):
                async with client.stream(
                    method, path, json=payload, content=content, headers=headers
                ) as response:
                    if response.status_code not in accepted:
                        raise BusinessFailure(f"http_{response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > 2 * 1024 * 1024:
                            raise BusinessFailure("response_too_large")
                        body.extend(chunk)
                    return httpx.Response(
                        response.status_code, headers=response.headers, content=bytes(body)
                    )
        except (httpx.TimeoutException, TimeoutError):
            raise BusinessFailure("request_timeout") from None
        except httpx.HTTPError:
            raise BusinessFailure("transport_error") from None


def decode[T: BaseModel](model: type[T], response: httpx.Response) -> T:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise BusinessFailure("invalid_response") from None


@dataclass(frozen=True)
class UploadReceipt:
    session_id: UUID
    document_id: UUID
    version_id: UUID


async def upload_case(
    io: BusinessIO, case: LoadedCase, key: str, refs: dict[str, str]
) -> UploadReceipt:
    async with io.client() as api, io.client(objects=True) as objects:
        created = decode(
            UploadSessionCreateResponse,
            await io.request(
                api,
                "POST",
                "/api/upload-sessions",
                payload={
                    "filename": Path(case.spec.path).name,
                    "sizeBytes": len(case.content),
                    "mediaType": case.spec.media_type,
                    "sha256": case.spec.sha256,
                },
                headers={"Idempotency-Key": key},
                accepted=(200, 201),
            ),
        )
        refs["session_id"] = str(created.session_id)
        part_size = created.part_size_bytes
        if (
            created.size_bytes != len(case.content)
            or created.declared_sha256 != case.spec.sha256
            or part_size <= 0
            or created.expected_part_count != (len(case.content) + part_size - 1) // part_size
            or not 1 <= created.expected_part_count <= 16
        ):
            raise BusinessFailure("upload_contract_mismatch")
        session_path = f"/api/upload-sessions/{created.session_id}"
        parts = []
        for number in range(1, created.expected_part_count + 1):
            part = case.content[(number - 1) * part_size : number * part_size]
            checksum = base64.b64encode(hashlib.sha256(part).digest()).decode("ascii")
            signed = decode(
                PresignUploadPartResponse,
                await io.request(
                    api,
                    "POST",
                    f"{session_path}/parts/{number}/presign",
                    payload={"sizeBytes": len(part), "checksumSha256": checksum},
                ),
            )
            url = urlsplit(signed.url)
            if (
                f"{url.scheme}://{url.netloc}"
                not in {loopback_origin(o) for o in io.plan.object_origins}
                or url.username is not None
                or url.password is not None
                or url.fragment
                or signed.part_number != number
                or signed.size_bytes != len(part)
                or signed.checksum_sha256 != checksum
                or any(
                    k.lower()
                    in {
                        "authorization",
                        "cookie",
                        "proxy-authorization",
                        "host",
                        "x-session-context",
                        "x-csrf-token",
                    }
                    for k in signed.headers
                )
            ):
                raise BusinessFailure("object_request_rejected")
            sent = await io.request(
                objects, "PUT", signed.url, content=part, headers=signed.headers
            )
            etag = sent.headers.get("etag")
            if not etag:
                raise BusinessFailure("object_etag_missing")
            parts.append(
                {
                    "partNumber": number,
                    "sizeBytes": len(part),
                    "etag": etag,
                    "checksumSha256": checksum,
                }
            )
        completed = decode(
            UploadSessionCompleteResponse,
            await io.request(api, "POST", f"{session_path}/complete", payload={"parts": parts}),
        )
        if completed.session_id != created.session_id or completed.status != "completed":
            raise BusinessFailure("upload_completion_mismatch")
        refs.update(version_id=str(completed.version_id), document_id=str(completed.document_id))
        return UploadReceipt(created.session_id, completed.document_id, completed.version_id)


class BusinessObserver(Protocol):
    """Target-local DB/object/retrieval observations, never a product HTTP endpoint."""

    async def ingestion(self, version_id: UUID, case: LoadedCase) -> dict[str, Any] | None: ...
    async def retrieve(self, version_id: UUID, case: LoadedCase) -> dict[str, Any]: ...
    async def ledger(self, row_id: UUID, attempt_id: UUID) -> dict[str, Any]: ...


async def wait_ingestion(
    io: BusinessIO, observer: BusinessObserver, receipt: UploadReceipt, case: LoadedCase
) -> dict[str, Any]:
    async with io.client() as client:
        while True:
            response = await io.request(client, "GET", "/api/documents?limit=200")
            try:
                versions = TypeAdapter(list[DocumentInventoryItemResponse]).validate_json(
                    response.content
                )
            except ValidationError:
                raise BusinessFailure("invalid_inventory") from None
            owned = [v for v in versions if v.version_id == receipt.version_id]
            if len(owned) > 1:
                raise BusinessFailure("duplicate_version")
            if owned:
                version = owned[0]
                if (
                    version.document_id != receipt.document_id
                    or version.size_bytes != case.spec.size_bytes
                ):
                    raise BusinessFailure("inventory_identity_mismatch")
                if version.version_status == "failed" or version.ingestion_status == "failed":
                    raise BusinessFailure("ingestion_failed")
                if version.version_status == "ready":
                    observed = await observer.ingestion(receipt.version_id, case)
                    if observed is None:
                        await asyncio.sleep(io.plan.poll_seconds)
                        continue
                    if observed.get("generation_id") != str(version.generation_id):
                        raise BusinessFailure("generation_identity_mismatch")
                    return observed
            await asyncio.sleep(io.plan.poll_seconds)


async def generate_and_recover(
    io: BusinessIO,
    observer: BusinessObserver,
    receipt: UploadReceipt,
    case: LoadedCase,
    key: str,
    refs: dict[str, str],
    retrieval: dict[str, Any],
    measured: dict[str, Any],
) -> dict[str, Any]:
    async with io.client() as client:
        packet = decode(
            PacketView,
            await io.request(
                client,
                "POST",
                "/api/presales",
                payload={
                    "title": "Synthetic capacity " + case.spec.key,
                    "sources": [
                        {
                            "versionId": str(receipt.version_id),
                            "applicability": "Controlled local capacity fixture",
                        }
                    ],
                    "requirements": [{"key": "R1", "text": case.spec.query}],
                },
                headers={"Idempotency-Key": key + "-packet"},
                accepted=(201,),
            ),
        )
        if (
            packet.generation_mode != "background"
            or len(packet.rows) != 1
            or len(packet.sources) != 1
            or packet.sources[0].version_id != receipt.version_id
            or packet.sources[0].content_sha256 != case.spec.sha256
        ):
            raise BusinessFailure("packet_contract_mismatch")
        row_id = packet.rows[0].id
        refs.update(packet_id=str(packet.id), row_id=str(row_id))
        path = f"/api/presales/{packet.id}"
        generate_path = f"{path}/rows/{row_id}/generate"
        headers = {"Idempotency-Key": key + "-generate"}
        generation_started = time.perf_counter()
        accepted = await io.request(
            client, "POST", generate_path, payload={}, headers=headers, accepted=(200, 202)
        )
        measured.update(
            accept_duration_ms=(time.perf_counter() - generation_started) * 1000,
            accept_status=accepted.status_code,
        )
        current = decode(PacketView, accepted)

    def row(view: PacketView) -> RowView:
        if (
            view.id != packet.id
            or len(view.rows) != 1
            or view.rows[0].id != row_id
            or view.sources != packet.sources
        ):
            raise BusinessFailure("packet_identity_mismatch")
        result = view.rows[0]
        if len(result.attempts) != 1:
            raise BusinessFailure("generation_attempt_mismatch")
        return result

    attempt_id = row(current).attempts[0].id
    refs["attempt_id"] = str(attempt_id)
    # Fresh client: recover from durable API state, without sharing prior in-memory responses.
    async with io.client() as client:
        current = decode(
            PacketView,
            await io.request(
                client, "POST", generate_path, payload={}, headers=headers, accepted=(200, 202)
            ),
        )
        while True:
            latest = row(current)
            if latest.attempts[0].id != attempt_id:
                raise BusinessFailure("replay_created_attempt")
            if latest.state in {"drafted", "failed"}:
                break
            await asyncio.sleep(io.plan.poll_seconds)
            current = decode(PacketView, await io.request(client, "GET", path))
        measured["terminal_duration_ms"] = (time.perf_counter() - generation_started) * 1000
        if latest.state != case.spec.expected_terminal:
            raise BusinessFailure("unexpected_generation_outcome")
        replay = row(
            decode(
                PacketView,
                await io.request(
                    client, "POST", generate_path, payload={}, headers=headers, accepted=(200, 202)
                ),
            )
        )
        if replay != latest:
            raise BusinessFailure("terminal_replay_changed_result")
        ledger = await observer.ledger(row_id, attempt_id)
        failed = latest.state == "failed"
        if (
            ledger.get("attempts") != 1
            or ledger.get("reservation_state") != ("released" if failed else "consumed")
            or ledger.get("reserved_quantity") != 1
            or ledger.get("consume_events") != int(not failed)
            or ledger.get("release_events") != int(failed)
            or ledger.get("consumed_quantity") != int(not failed)
            or ledger.get("released_quantity") != int(failed)
            or not case.spec.min_provider_calls
            <= ledger.get("provider_calls", -1)
            <= case.spec.max_provider_calls
            or ledger["provider_calls"] != latest.attempts[0].provider_request_count
            or ledger.get("call_states", {}).get("running", 1) != 0
            or ledger.get("call_states", {}).get("unknown", 1) != 0
        ):
            raise BusinessFailure("generation_accounting_mismatch")
        if failed:
            if (
                latest.draft is not None
                or latest.review is not None
                or not latest.attempts[0].error_code
            ):
                raise BusinessFailure("failed_result_not_visible")
            exported = await io.request(client, "GET", path + "/export?mode=draft")
        else:
            draft = latest.draft
            if (
                draft is None
                or not draft.citations
                or any(
                    c.document_version_id != receipt.version_id
                    or str(c.chunk_id) not in retrieval["candidate_ids"]
                    for c in draft.citations
                )
                or not any(case.spec.excerpt in c.excerpt for c in draft.citations)
            ):
                raise BusinessFailure("generated_evidence_mismatch")
            review = ReviewInput.model_validate(
                {
                    **draft.model_dump(
                        include={
                            "status",
                            "answer",
                            "conditions",
                            "missing_information",
                            "prerequisites",
                        }
                    ),
                    "expected_revision": latest.revision,
                    "note": "Controlled local capacity check",
                }
            )
            review_payload = review.model_dump(mode="json", by_alias=True)
            review_headers = {"Idempotency-Key": key + "-review"}
            reviewed = row(
                decode(
                    PacketView,
                    await io.request(
                        client,
                        "PUT",
                        path + f"/rows/{row_id}/review",
                        payload=review_payload,
                        headers=review_headers,
                    ),
                )
            )
            repeated = row(
                decode(
                    PacketView,
                    await io.request(
                        client,
                        "PUT",
                        path + f"/rows/{row_id}/review",
                        payload=review_payload,
                        headers=review_headers,
                    ),
                )
            )
            refreshed = row(decode(PacketView, await io.request(client, "GET", path)))
            if (
                reviewed.review is None
                or len(reviewed.review_history) != 1
                or reviewed.draft != draft
                or repeated != reviewed
                or refreshed != reviewed
                or reviewed.review.answer != draft.answer
                or reviewed.review.prerequisites != draft.prerequisites
            ):
                raise BusinessFailure("review_recovery_mismatch")
            exported = await io.request(client, "GET", path + "/export?mode=reviewed")
        try:
            rows = list(csv.DictReader(textio.StringIO(exported.content.decode("utf-8-sig"))))
        except (ValueError, UnicodeDecodeError):
            raise BusinessFailure("invalid_export") from None
        if len(rows) != 1 or rows[0].get("要求编号") != "R1":
            raise BusinessFailure("export_row_mismatch")
        if failed:
            if rows[0].get("判断") != "未生成":
                raise BusinessFailure("failed_export_mismatch")
        elif (
            latest.draft is None
            or rows[0].get("复核状态") != "已复核"
            or case.spec.excerpt not in rows[0].get("原文证据", "")
            or rows[0].get("响应文案") != latest.draft.answer
        ):
            raise BusinessFailure("reviewed_export_mismatch")
        return {
            "terminal_state": latest.state,
            "same_key_replays": 2,
            "refreshed_from_new_client": True,
            "ledger": ledger,
            "reviewed": not failed,
            "csv_sha256": hashlib.sha256(exported.content).hexdigest(),
        }


def _empty_sample(index: int, phase: str, repetition: int, case: LoadedCase) -> dict[str, Any]:
    return {
        "task_index": index,
        "phase": phase,
        "repetition": repetition,
        "case": case.spec.key,
        "fixture_sha256": case.spec.sha256,
        "fault_label": case.spec.fault_label,
        "expected_terminal": case.spec.expected_terminal,
        "status": "not_run",
        "expectation_met": False,
        "business_success": False,
        "duration_ms": None,
        "resource_refs": {},
        "boundaries": {
            name: {"status": "not_run", "duration_ms": None, "reason": "upstream_not_completed"}
            for name in BOUNDARIES
        },
    }


async def _execute_task(
    io: BusinessIO,
    observer: BusinessObserver,
    case: LoadedCase,
    sample: dict[str, Any],
    key: str,
    remaining_seconds: float,
) -> None:
    if io.exhausted or io.requests >= io.plan.max_http_requests or remaining_seconds <= 0:
        sample["reason"] = "run_budget_exhausted"
        return
    started = time.perf_counter()
    active = "upload"
    stage_started = started
    refs = sample["resource_refs"]

    async def measure(name: str, seconds: float, operation: Callable[[], Awaitable[Any]]) -> Any:
        nonlocal active, stage_started
        active, stage_started = name, time.perf_counter()
        entry = sample["boundaries"][name]
        entry["started_at"] = datetime.now(UTC).isoformat()
        async with asyncio.timeout(seconds):
            result = await operation()
        entry.update(
            status="passed",
            reason=None,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            completed_at=datetime.now(UTC).isoformat(),
        )
        return result

    try:
        async with asyncio.timeout(min(io.plan.task_timeout_seconds, remaining_seconds)):
            receipt = await measure(
                "upload",
                io.plan.upload_timeout_seconds,
                lambda: upload_case(io, case, key + "-upload", refs),
            )
            ingestion = await measure(
                "ingestion",
                io.plan.ingestion_timeout_seconds,
                lambda: wait_ingestion(io, observer, receipt, case),
            )
            sample["ingestion"] = ingestion
            retrieval = await measure(
                "retrieval",
                io.plan.retrieval_timeout_seconds,
                lambda: observer.retrieve(receipt.version_id, case),
            )
            sample["retrieval"] = retrieval
            if retrieval.get("generation_ids") != [ingestion["generation_id"]]:
                raise BusinessFailure("retrieval_generation_drift")
            generation = await measure(
                "generation_recovery",
                io.plan.generation_timeout_seconds,
                lambda: generate_and_recover(
                    io,
                    observer,
                    receipt,
                    case,
                    key,
                    refs,
                    retrieval,
                    sample["boundaries"]["generation_recovery"],
                ),
            )
            sample["generation"] = generation
            sample["business_success"] = case.spec.expected_terminal == "drafted"
            sample["expectation_met"] = True
            sample["status"] = "passed" if sample["business_success"] else "expected_failure"
            if not sample["business_success"]:
                sample["boundaries"]["generation_recovery"]["status"] = "expected_failure"
    except asyncio.CancelledError:
        sample.update(status="interrupted", reason="run_interrupted")
        sample["boundaries"][active].update(
            status="interrupted",
            reason="run_interrupted",
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            completed_at=datetime.now(UTC).isoformat(),
        )
        raise
    except (BusinessFailure, TimeoutError) as error:
        code = "boundary_timeout" if isinstance(error, TimeoutError) else str(error)
        sample.update(status="failed", reason=code)
        sample["boundaries"][active].update(
            status="failed",
            reason=code,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            completed_at=datetime.now(UTC).isoformat(),
        )
    except Exception as error:
        sample.update(status="failed", reason="unexpected_error", error_type=type(error).__name__)
        sample["boundaries"][active].update(
            status="failed",
            reason="unexpected_error",
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        sample["duration_ms"] = (time.perf_counter() - started) * 1000


def summarize_business_samples(samples: list[dict[str, Any]], plan: BusinessPlan) -> dict[str, Any]:
    boundaries = {}
    targets = dict(
        zip(
            BOUNDARIES,
            (
                plan.upload_target_ms,
                plan.ingestion_target_ms,
                plan.retrieval_target_ms,
                plan.generation_timeout_seconds * 1000,
            ),
            strict=True,
        )
    )
    for name in BOUNDARIES:
        items = [sample["boundaries"][name] for sample in samples]
        durations = [item["duration_ms"] for item in items if item["duration_ms"] is not None]
        latency = build_percentile_summary(durations)
        p95 = latency["p95_ms"]
        boundaries[name] = {
            "planned": len(items),
            "observed": len(durations),
            **{
                state: sum(item["status"] == state for item in items)
                for state in ("passed", "failed", "expected_failure", "interrupted", "not_run")
            },
            **latency,
            "proposed_target_ms": targets[name],
            "proposed_target_met": p95 is not None and p95 <= targets[name],
        }
    accepts = [
        item["boundaries"]["generation_recovery"]["accept_duration_ms"]
        for item in samples
        if "accept_duration_ms" in item["boundaries"]["generation_recovery"]
    ]
    terminals = [
        item["boundaries"]["generation_recovery"]["terminal_duration_ms"]
        for item in samples
        if "terminal_duration_ms" in item["boundaries"]["generation_recovery"]
    ]
    normal = [item for item in samples if item["expected_terminal"] == "drafted"]
    successful = sum(item["business_success"] for item in samples)
    return {
        "planned_tasks": len(samples),
        "business_successes": successful,
        "business_non_successes": len(samples) - successful,
        "expected_failures": sum(item["status"] == "expected_failure" for item in samples),
        "not_run": sum(item["status"] == "not_run" for item in samples),
        "interrupted": sum(item["status"] == "interrupted" for item in samples),
        "expectations_met": sum(item["expectation_met"] for item in samples),
        "normal_failure_rate": sum(not item["business_success"] for item in normal) / len(normal)
        if normal
        else None,
        "boundaries": boundaries,
        "generation_acceptance": {
            "observed": len(accepts),
            **build_percentile_summary(accepts),
            "proposed_target_ms": plan.accept_target_ms,
        },
        "generation_terminal": {
            "observed": len(terminals),
            **build_percentile_summary(terminals),
        },
    }


async def run_business_matrix(
    loaded: LoadedBusinessPlan,
    token: str,
    observer: BusinessObserver,
    *,
    api_transport: httpx.AsyncBaseTransport | None = None,
    object_transport: httpx.AsyncBaseTransport | None = None,
    io: BusinessIO | None = None,
    on_sample: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    io = io or BusinessIO(
        loaded.plan, token, api_transport=api_transport, object_transport=object_transport
    )
    if io.plan != loaded.plan or io.token != token or not token:
        raise ValueError("business_client_mismatch")
    started = datetime.now(UTC).isoformat()
    deadline = time.monotonic() + loaded.plan.max_run_seconds
    nonce = uuid4().hex
    samples: list[dict[str, Any]] = []
    groups: list[tuple[BusinessPhase, int, list[tuple[LoadedCase, dict[str, Any]]]]] = []
    for repetition in range(1, loaded.plan.repetitions + 1):
        for phase in loaded.plan.phases:
            phase_samples = []
            for _ in range(phase.tasks):
                index = len(samples)
                case = loaded.cases[index % len(loaded.cases)]
                sample = _empty_sample(index, phase.name, repetition, case)
                samples.append(sample)
                phase_samples.append((case, sample))
            groups.append((phase, repetition, phase_samples))

    phase_times: dict[tuple[int, str], dict[str, Any]] = {}
    recorded: set[int] = set()
    interruption: str | None = None
    try:
        for phase, repetition, phase_samples in groups:
            semaphore = asyncio.Semaphore(phase.concurrency)
            phase_started = time.perf_counter()
            window = {"started_at": datetime.now(UTC).isoformat()}

            async def execute(
                case: LoadedCase, sample: dict[str, Any], gate: asyncio.Semaphore
            ) -> None:
                async with gate:
                    await _execute_task(
                        io,
                        observer,
                        case,
                        sample,
                        f"cap-{nonce}-{sample['task_index']}",
                        deadline - time.monotonic(),
                    )
                    if on_sample is not None:
                        on_sample(sample)
                        recorded.add(sample["task_index"])

            try:
                async with asyncio.TaskGroup() as tasks:
                    for case, sample in phase_samples:
                        tasks.create_task(execute(case, sample, semaphore))
            finally:
                phase_times[(repetition, phase.name)] = {
                    **window,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "duration_seconds": time.perf_counter() - phase_started,
                }
    except asyncio.CancelledError:
        interruption = "run_cancelled"
    except Exception:
        interruption = "sample_execution_or_journal_failed"
    for sample in samples:
        if sample["status"] == "not_run" and interruption:
            sample["reason"] = "run_interrupted"
        if on_sample is not None and sample["task_index"] not in recorded:
            try:
                on_sample(sample)
            except Exception:
                interruption = "sample_journal_failed"
                break
    phases = [
        {
            "phase": phase.name,
            "repetition": repetition,
            **phase_times.get(
                (repetition, phase.name),
                {
                    "started_at": None,
                    "completed_at": None,
                    "duration_seconds": None,
                },
            ),
            "summary": summarize_business_samples([s for _, s in items], loaded.plan),
        }
        for phase, repetition, items in groups
    ]
    summary = summarize_business_samples(samples, loaded.plan)
    return {
        "schema_version": 1,
        "scope": "local-controlled-business-sampler",
        "status": "interrupted"
        if interruption
        else "local_checks_passed"
        if summary["expectations_met"] == loaded.plan.total_tasks
        else "local_checks_failed",
        "production_capacity_approved": False,
        "interruption_reason": interruption,
        "plan_sha256": loaded.sha256,
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "http_requests": io.requests,
        "http_budget": loaded.plan.max_http_requests,
        "samples": samples,
        "phases": phases,
        "cases": [
            {
                "case": case.spec.key,
                "summary": summarize_business_samples(
                    [s for s in samples if s["case"] == case.spec.key], loaded.plan
                ),
            }
            for case in loaded.cases
        ],
        "summary": summary,
        "limitations": [
            "Local functional measurements; proposed targets are not approved capacity objectives.",
            "Core retrieval runs in the observer process, "
            "separately from generation's internal retrieval.",
            "New-client refresh and fixed-key replays do not prove browser or proxy recovery.",
            "No node or complete Prometheus telemetry, independent review, "
            "supplier cost or production approval.",
            "Failure scenarios must be configured at the controlled provider boundary; "
            "the sampler does not inject faults.",
        ],
    }
