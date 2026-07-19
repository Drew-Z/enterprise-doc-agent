from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import psutil

from enterprise_doc_core.evaluation import (
    LoadReport,
    ReportProvenance,
    build_percentile_summary,
    capture_report_provenance,
    nearest_rank_percentile,
    seal_report,
)
from enterprise_doc_core.evaluation.contracts import utc_now

_TERMINAL_STATUSES = {"cancelled", "expired", "failed", "refused", "rejected", "succeeded"}
_HOST_CPU_P95_TARGET = 85.0
_HOST_MEMORY_P95_TARGET = 90.0
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class RequestSample:
    duration_ms: float
    success: bool
    status_code: int | None
    terminal_status: str | None = None
    identity: tuple[str, str] | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceSample:
    host_cpu_percent: float
    host_memory_percent: float
    process_cpu_percent: float | None
    process_rss_bytes: int | None
    process_threads: int | None


def _numeric_summary(values: list[float | int]) -> dict[str, float | int | None]:
    normalized = [float(value) for value in values]
    return {
        "sample_count": len(normalized),
        "min": min(normalized) if normalized else None,
        "average": sum(normalized) / len(normalized) if normalized else None,
        "p95": nearest_rank_percentile(normalized, 0.95),
        "max": max(normalized) if normalized else None,
    }


def _resolve_listener_pid(base_url: str) -> int | None:
    parsed = urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        connections = psutil.net_connections(kind="inet")
    except psutil.Error:
        return None
    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or connection.pid is None:
            continue
        if connection.laddr and connection.laddr.port == port:
            return connection.pid
    return None


def _collect_resource_sample(
    process: psutil.Process | None,
    *,
    interval_seconds: float,
) -> ResourceSample:
    host_cpu = psutil.cpu_percent(interval=interval_seconds)
    host_memory = psutil.virtual_memory()
    process_cpu: float | None = None
    process_rss: int | None = None
    process_threads: int | None = None
    if process is not None:
        try:
            with process.oneshot():
                process_cpu = process.cpu_percent(interval=None)
                process_rss = process.memory_info().rss
                process_threads = process.num_threads()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            process = None
    return ResourceSample(
        host_cpu_percent=host_cpu,
        host_memory_percent=host_memory.percent,
        process_cpu_percent=process_cpu,
        process_rss_bytes=process_rss,
        process_threads=process_threads,
    )


async def sample_resources(
    *,
    stop: asyncio.Event,
    process_id: int | None,
    process_discovery: str,
    interval_seconds: float,
) -> dict[str, Any]:
    process: psutil.Process | None = None
    if process_id is not None:
        try:
            process = psutil.Process(process_id)
            process.cpu_percent(interval=None)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            process = None

    samples: list[ResourceSample] = []
    while True:
        sample = await asyncio.to_thread(
            _collect_resource_sample,
            process,
            interval_seconds=interval_seconds,
        )
        samples.append(sample)
        if stop.is_set():
            break

    process_cpu = [
        sample.process_cpu_percent for sample in samples if sample.process_cpu_percent is not None
    ]
    process_rss = [
        sample.process_rss_bytes for sample in samples if sample.process_rss_bytes is not None
    ]
    process_threads = [
        sample.process_threads for sample in samples if sample.process_threads is not None
    ]
    return {
        "measured": True,
        "scope": "load-generator-host-and-selected-process",
        "sample_interval_seconds": interval_seconds,
        "sample_count": len(samples),
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "host_memory_total_bytes": psutil.virtual_memory().total,
        "process": {
            "pid_recorded": process_id is not None,
            "discovery": process_discovery,
            "sampled": bool(process_cpu or process_rss or process_threads),
        },
        "host_cpu_percent": _numeric_summary([sample.host_cpu_percent for sample in samples]),
        "host_memory_percent": _numeric_summary([sample.host_memory_percent for sample in samples]),
        "process_cpu_percent": _numeric_summary(process_cpu),
        "process_rss_bytes": _numeric_summary(process_rss),
        "process_threads": _numeric_summary(process_threads),
    }


def _resource_sampling_failure(error: BaseException) -> dict[str, Any]:
    return {
        "measured": False,
        "reason": f"Resource sampler failed: {type(error).__name__}: {error}",
    }


async def _finish_resource_sampling(
    *,
    stop: asyncio.Event,
    task: asyncio.Task[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if task is None:
        return None
    stop.set()
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as error:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass
            raise
        return _resource_sampling_failure(error)
    except Exception as error:
        return _resource_sampling_failure(error)


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return "invalid_json"
    if not isinstance(payload, dict):
        return "invalid_json"
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return str(error["code"])
    return "unexpected_response"


async def _poll_terminal(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    headers: dict[str, str],
    timeout_seconds: float,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/api/agent-runs/{run_id}", headers=headers)
        if response.status_code != 200:
            return f"http_{response.status_code}:{_error_code(response)}"
        payload = response.json()
        status = payload.get("status") if isinstance(payload, dict) else None
        if isinstance(status, str) and status in _TERMINAL_STATUSES:
            return status
        await asyncio.sleep(poll_seconds)
    return "timeout"


async def execute_request(
    client: httpx.AsyncClient,
    *,
    scenario: str,
    index: int,
    token: str | None,
    document_version_id: str | None,
    run_id: str | None,
    shared_idempotency_key: str,
    terminal_timeout_seconds: float,
    poll_seconds: float,
) -> RequestSample:
    started = time.perf_counter()
    headers = _auth_headers(token)
    status_code: int | None = None
    terminal_status: str | None = None
    identity: tuple[str, str] | None = None
    try:
        if scenario == "health":
            response = await client.get("/health/live")
            status_code = response.status_code
            success = response.status_code == 200
        elif scenario == "ready":
            response = await client.get("/health/ready")
            status_code = response.status_code
            success = response.status_code == 200
        elif scenario == "status":
            assert run_id is not None
            response = await client.get(f"/api/agent-runs/{run_id}", headers=headers)
            status_code = response.status_code
            success = response.status_code == 200
        else:
            assert document_version_id is not None
            idempotency_key = (
                shared_idempotency_key
                if scenario == "duplicate-agent-create"
                else f"m5-load-{index}-{uuid4().hex}"
            )
            response = await client.post(
                "/api/agent-runs",
                headers={**headers, "Idempotency-Key": idempotency_key},
                json={
                    "documentVersionId": document_version_id,
                    "taskType": "question_answer",
                    "inputText": "Summarize the authorized evidence.",
                    "publishRequested": False,
                },
            )
            status_code = response.status_code
            success = response.status_code in {200, 202}
            payload = response.json() if success else {}
            if isinstance(payload, dict):
                raw_run_id = payload.get("runId")
                raw_job_id = payload.get("jobId")
                if isinstance(raw_run_id, str) and isinstance(raw_job_id, str):
                    identity = (raw_run_id, raw_job_id)
                    if scenario == "end-to-end":
                        terminal_status = await _poll_terminal(
                            client,
                            run_id=raw_run_id,
                            headers=headers,
                            timeout_seconds=terminal_timeout_seconds,
                            poll_seconds=poll_seconds,
                        )
                        success = terminal_status in {"succeeded", "refused"}
                else:
                    success = False
        return RequestSample(
            duration_ms=(time.perf_counter() - started) * 1000,
            success=success,
            status_code=status_code,
            terminal_status=terminal_status,
            identity=identity,
        )
    except (httpx.HTTPError, json.JSONDecodeError, AssertionError):
        return RequestSample(
            duration_ms=(time.perf_counter() - started) * 1000,
            success=False,
            status_code=status_code,
            terminal_status=terminal_status,
            identity=identity,
        )


async def run_workload(
    client: httpx.AsyncClient,
    *,
    scenario: str,
    requests: int,
    concurrency: int,
    token: str | None,
    document_version_id: str | None,
    run_id: str | None,
    terminal_timeout_seconds: float,
    poll_seconds: float,
) -> tuple[list[RequestSample], float]:
    semaphore = asyncio.Semaphore(concurrency)
    shared_key = f"m5-load-duplicate-{uuid4().hex}"

    async def bounded(index: int) -> RequestSample:
        async with semaphore:
            return await execute_request(
                client,
                scenario=scenario,
                index=index,
                token=token,
                document_version_id=document_version_id,
                run_id=run_id,
                shared_idempotency_key=shared_key,
                terminal_timeout_seconds=terminal_timeout_seconds,
                poll_seconds=poll_seconds,
            )

    started = time.perf_counter()
    results = await asyncio.gather(
        *(bounded(index) for index in range(requests)),
        return_exceptions=True,
    )
    samples: list[RequestSample] = []
    for result in results:
        if isinstance(result, Exception):
            samples.append(
                RequestSample(
                    duration_ms=0.0,
                    success=False,
                    status_code=None,
                    error_code=_execution_error_code(result),
                )
            )
        elif isinstance(result, BaseException):
            raise result
        else:
            samples.append(result)
    return samples, time.perf_counter() - started


def _resource_value(
    resource_saturation: dict[str, Any],
    section: str,
    field: str,
) -> float | None:
    summary = resource_saturation.get(section)
    if not isinstance(summary, dict):
        return None
    value = summary.get(field)
    return float(value) if isinstance(value, (float, int)) else None


def _resource_bottleneck(resource_saturation: dict[str, Any]) -> str | None:
    if resource_saturation.get("measured") is not True:
        return None
    host_cpu_p95 = _resource_value(resource_saturation, "host_cpu_percent", "p95")
    host_memory_p95 = _resource_value(resource_saturation, "host_memory_percent", "p95")
    if host_cpu_p95 is not None and host_cpu_p95 > _HOST_CPU_P95_TARGET:
        return f"Host CPU saturation was observed with p95 {host_cpu_p95:.1f}%."
    if host_memory_p95 is not None and host_memory_p95 > _HOST_MEMORY_P95_TARGET:
        return f"Host memory pressure was observed with p95 {host_memory_p95:.1f}%."
    return None


def _resource_capacity_clause(resource_saturation: dict[str, Any]) -> str:
    if resource_saturation.get("measured") is not True:
        return "No resource sampler was attached."
    sample_count = resource_saturation.get("sample_count")
    host_cpu_p95 = _resource_value(resource_saturation, "host_cpu_percent", "p95")
    host_memory_p95 = _resource_value(resource_saturation, "host_memory_percent", "p95")
    process_rss_max = _resource_value(resource_saturation, "process_rss_bytes", "max")
    values = [f"{sample_count} resource samples"]
    if host_cpu_p95 is not None:
        values.append(f"host CPU p95 {host_cpu_p95:.1f}%")
    if host_memory_p95 is not None:
        values.append(f"host memory p95 {host_memory_p95:.1f}%")
    if process_rss_max is not None:
        values.append(f"selected-process RSS max {process_rss_max:.0f} bytes")
    return ", ".join(values) + "."


def build_report(
    *,
    scenario: str,
    requests: int,
    concurrency: int,
    base_url: str,
    samples: list[RequestSample],
    duration_seconds: float,
    started_at: str,
    completed_at: str,
    target_p95_ms: float,
    resource_saturation: dict[str, Any] | None = None,
    provenance: ReportProvenance | None = None,
    execution_error_code: str | None = None,
) -> LoadReport:
    successful = sum(sample.success for sample in samples)
    failed = len(samples) - successful
    durations = [sample.duration_ms for sample in samples]
    latency = build_percentile_summary(durations)
    p95 = latency["p95_ms"]
    status_counts = Counter(
        sample.error_code
        or (str(sample.status_code) if sample.status_code is not None else "transport_error")
        for sample in samples
        if not sample.success
    )
    if execution_error_code is not None:
        status_counts[execution_error_code] += 1
    terminal_counts = Counter(
        sample.terminal_status for sample in samples if sample.terminal_status is not None
    )
    identities = {sample.identity for sample in samples if sample.identity is not None}
    duplicate_consistent = scenario != "duplicate-agent-create" or len(identities) == 1
    latency_target_passed = isinstance(p95, (float, int)) and p95 <= target_p95_ms
    resources_requested = resource_saturation is not None
    resolved_resources = resource_saturation or {
        "measured": False,
        "reason": "No process or host resource sampler was attached to this run.",
    }
    resources_measured = resolved_resources.get("measured") is True
    host_cpu_p95 = _resource_value(resolved_resources, "host_cpu_percent", "p95")
    host_memory_p95 = _resource_value(resolved_resources, "host_memory_percent", "p95")
    resource_targets_passed = not resources_requested or (
        resources_measured
        and host_cpu_p95 is not None
        and host_cpu_p95 <= _HOST_CPU_P95_TARGET
        and host_memory_p95 is not None
        and host_memory_p95 <= _HOST_MEMORY_P95_TARGET
    )
    passed = (
        execution_error_code is None
        and failed == 0
        and duplicate_consistent
        and latency_target_passed
        and resource_targets_passed
    )
    resource_bottleneck = _resource_bottleneck(resolved_resources)
    if execution_error_code is not None:
        bottleneck = "The load runner failed before the configured workload completed."
    elif failed:
        bottleneck = "Request failures or terminal timeouts dominate this bounded run."
    elif resources_requested and not resources_measured:
        bottleneck = "Requested resource sampling did not produce usable measurements."
    elif resource_bottleneck is not None:
        bottleneck = resource_bottleneck
    elif isinstance(p95, float) and p95 > target_p95_ms:
        bottleneck = (
            "Observed p95 exceeds the configured local target; profile the slow endpoint "
            "and dependencies."
        )
    else:
        bottleneck = "No bottleneck was inferred from latency, errors, or sampled resources."
    throughput = len(samples) / duration_seconds if duration_seconds > 0 else 0.0
    resource_limitation = (
        "Host and selected-process resources were sampled, but database pool/locks, Redis, "
        "queue depth, object-store saturation, and model-provider saturation were not."
        if resources_measured
        else (
            "CPU, memory, queue depth, database saturation, and dependency saturation were "
            "not sampled by this command."
        )
    )
    targets: dict[str, float] = {
        "p95_ms": target_p95_ms,
        "error_rate": 0.0,
    }
    if resources_requested:
        targets.update(
            {
                "host_cpu_p95_percent": _HOST_CPU_P95_TARGET,
                "host_memory_p95_percent": _HOST_MEMORY_P95_TARGET,
            }
        )
    resolved_provenance = provenance or capture_report_provenance(
        command=["internal", "scripts.load_m5.build_report"],
        root=ROOT,
        execution_scope="local-bounded-load",
    )
    return seal_report(
        LoadReport(
            scenario=scenario,
            status="passed" if passed else "failed",
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round(duration_seconds, 6),
            environment={
                "operating_system": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "network_scope": "local-or-explicit-base-url",
            },
            workload={
                "base_url": base_url,
                "configured_requests": requests,
                "configured_concurrency": concurrency,
                "percentile_method": "nearest-rank",
                "execution_error_code": execution_error_code,
                "sample_collection_complete": execution_error_code is None,
            },
            completed_requests=len(samples),
            successful_requests=successful,
            failed_requests=failed,
            error_rate=(
                1.0 if execution_error_code is not None else failed / len(samples) if samples else 0
            ),
            throughput_requests_per_second=round(throughput, 6),
            latency_ms=latency,
            errors_by_status=dict(status_counts),
            terminal_status_counts=dict(terminal_counts),
            resource_saturation=resolved_resources,
            bottleneck=bottleneck,
            capacity_conclusion=(
                (
                    "The load runner stopped before a complete sample set was returned; "
                    f"{len(samples)} completed samples were recorded. "
                    "No throughput or capacity conclusion is claimed from this failed run."
                )
                if execution_error_code is not None
                else (
                    f"This bounded run completed {len(samples)} requests at "
                    f"{throughput:.3f} requests/s in the recorded environment. "
                    f"{_resource_capacity_clause(resolved_resources)} "
                    "It is not a production capacity claim."
                )
            ),
            targets=targets,
            measured={
                "p95_ms": p95,
                "error_rate": (
                    1.0
                    if execution_error_code is not None
                    else failed / len(samples)
                    if samples
                    else 0
                ),
                "throughput_requests_per_second": round(throughput, 6),
                "duplicate_identity_count": len(identities),
                "host_cpu_p95_percent": host_cpu_p95,
                "host_memory_p95_percent": host_memory_p95,
            },
            limitations=[
                *(
                    [
                        "The load runner stopped before workload completion; the stable "
                        f"failure category was {execution_error_code}."
                    ]
                    if execution_error_code is not None
                    else []
                ),
                "This is one bounded execution and does not establish production capacity or "
                "an SLO.",
                resource_limitation,
                "Representative capacity requires repeated runs on an isolated production-like "
                "environment with immutable images.",
            ],
            provenance=resolved_provenance,
        )
    )


def _report_command(args: argparse.Namespace) -> list[str]:
    command = [
        "python",
        "scripts/load_m5.py",
        "--scenario",
        str(args.scenario),
        "--base-url",
        str(args.base_url),
        "--requests",
        str(args.requests),
        "--concurrency",
        str(args.concurrency),
        "--token-env",
        str(args.token_env),
        "--request-timeout-seconds",
        str(args.request_timeout_seconds),
        "--terminal-timeout-seconds",
        str(args.terminal_timeout_seconds),
        "--poll-seconds",
        str(args.poll_seconds),
        "--target-p95-ms",
        str(args.target_p95_ms),
        "--resource-sample-interval-seconds",
        str(args.resource_sample_interval_seconds),
    ]
    if args.document_version_id:
        command.extend(["--document-version-id", "<redacted-document-version-id>"])
    if args.run_id:
        command.extend(["--run-id", "<redacted-run-id>"])
    if args.sample_resources:
        command.append("--sample-resources")
    if args.resource_process_id is not None:
        command.extend(["--resource-process-id", str(args.resource_process_id)])
    report_path = getattr(args, "report_path", None)
    if report_path is not None:
        command.extend(["--report-path", "<report-path>"])
    return command


def _provenance_input_sha256(args: argparse.Namespace) -> str | None:
    sensitive_inputs = {
        "document_version_id": args.document_version_id,
        "run_id": args.run_id,
    }
    if not any(value is not None for value in sensitive_inputs.values()):
        return None
    encoded = json.dumps(
        sensitive_inputs,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution_error_code(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "load_http_timeout"
    if isinstance(error, httpx.HTTPError):
        return "load_http_error"
    if isinstance(error, OSError):
        return "load_os_error"
    return "load_runner_error"


def _validate_args(args: argparse.Namespace, token: str | None) -> None:
    if args.requests <= 0 or args.concurrency <= 0:
        raise SystemExit("requests and concurrency must be positive")
    if args.resource_process_id is not None and args.resource_process_id <= 0:
        raise SystemExit("--resource-process-id must be positive")
    if args.resource_sample_interval_seconds <= 0:
        raise SystemExit("--resource-sample-interval-seconds must be positive")
    if (
        args.scenario in {"status", "agent-create", "duplicate-agent-create", "end-to-end"}
        and not token
    ):
        raise SystemExit(f"set {args.token_env} for the authenticated scenario")
    if args.scenario == "status" and not args.run_id:
        raise SystemExit("--run-id is required for the status scenario")
    if (
        args.scenario in {"agent-create", "duplicate-agent-create", "end-to-end"}
        and not args.document_version_id
    ):
        raise SystemExit("--document-version-id is required for Agent create scenarios")


async def execute_load(args: argparse.Namespace) -> tuple[LoadReport, list[RequestSample]]:
    token = os.environ.get(args.token_env)
    _validate_args(args, token)
    started_at = utc_now()
    timeout = httpx.Timeout(args.request_timeout_seconds)
    resource_stop = asyncio.Event()
    resource_task: asyncio.Task[dict[str, Any]] | None = None
    if args.sample_resources:
        process_id = args.resource_process_id
        process_discovery = "explicit-pid"
        if process_id is None:
            process_id = _resolve_listener_pid(args.base_url)
            process_discovery = (
                "local-listener" if process_id is not None else "local-listener-not-found"
            )
        resource_task = asyncio.create_task(
            sample_resources(
                stop=resource_stop,
                process_id=process_id,
                process_discovery=process_discovery,
                interval_seconds=args.resource_sample_interval_seconds,
            )
        )
    resource_saturation: dict[str, Any] | None = None
    provenance = capture_report_provenance(
        command=_report_command(args),
        root=ROOT,
        execution_scope="local-bounded-load",
        input_sha256=_provenance_input_sha256(args),
    )
    samples: list[RequestSample] = []
    duration = 0.0
    execution_error_code: str | None = None
    workload_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=timeout, trust_env=False
        ) as client:
            samples, duration = await run_workload(
                client,
                scenario=args.scenario,
                requests=args.requests,
                concurrency=args.concurrency,
                token=token,
                document_version_id=args.document_version_id,
                run_id=args.run_id,
                terminal_timeout_seconds=args.terminal_timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        execution_error_code = _execution_error_code(error)
        duration = time.perf_counter() - workload_started
    finally:
        resource_saturation = await _finish_resource_sampling(
            stop=resource_stop,
            task=resource_task,
        )
    return (
        build_report(
            scenario=args.scenario,
            requests=args.requests,
            concurrency=args.concurrency,
            base_url=args.base_url,
            samples=samples,
            duration_seconds=duration,
            started_at=started_at,
            completed_at=utc_now(),
            target_p95_ms=args.target_p95_ms,
            resource_saturation=resource_saturation,
            provenance=provenance,
            execution_error_code=execution_error_code,
        ),
        samples,
    )


async def async_main(args: argparse.Namespace) -> LoadReport:
    report, _ = await execute_load(args)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded, report-producing M5 HTTP workload")
    parser.add_argument(
        "--scenario",
        choices=(
            "health",
            "ready",
            "status",
            "agent-create",
            "duplicate-agent-create",
            "end-to-end",
        ),
        default="health",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--token-env", default="ENTERPRISE_DOC_LOAD_TOKEN")
    parser.add_argument("--document-version-id")
    parser.add_argument("--run-id")
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--terminal-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--target-p95-ms", type=float, default=250.0)
    parser.add_argument("--sample-resources", action="store_true")
    parser.add_argument("--resource-process-id", type=int)
    parser.add_argument("--resource-sample-interval-seconds", type=float, default=0.25)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()

    report = asyncio.run(async_main(args))
    rendered = report.model_dump_json(indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report.status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
