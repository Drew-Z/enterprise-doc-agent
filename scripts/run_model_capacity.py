from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from enterprise_doc_core.evaluation import build_percentile_summary, nearest_rank_percentile

try:
    from scripts.validate_recovery_capacity_evidence import validate_evidence
except ModuleNotFoundError:
    from validate_recovery_capacity_evidence import validate_evidence

REQUIRED_PHASES = ("warmup", "steady_state", "burst", "recovery")
REQUIRED_TELEMETRY = ("gpu", "gpu_memory", "kv_cache", "queue")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_LABELS = {
    "actor_id",
    "document_id",
    "document_version_id",
    "run_id",
    "session_id",
    "tenant_id",
    "user_id",
}


class ModelCapacityError(ValueError):
    """Raised when model capacity execution cannot produce trustworthy evidence."""


@dataclass(frozen=True, slots=True)
class ModelRequestSample:
    success: bool
    status_code: int | None
    duration_ms: float
    ttft_ms: float | None
    tpot_ms: float | None
    output_tokens: int | None
    token_count_source: str
    error: str | None = None


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelCapacityError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelCapacityError(f"{label} must be a positive integer")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ModelCapacityError(f"{label} must be positive")
    return float(value)


def load_model_capacity_config(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ModelCapacityError(f"cannot read model capacity config: {error}") from error
    return validate_model_capacity_config(payload)


def validate_model_capacity_config(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ModelCapacityError("model capacity config must be an object")
    if payload.get("schema_version") != 1:
        raise ModelCapacityError("schema_version must be 1")
    prompts = payload.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise ModelCapacityError("prompts must be a non-empty list")
    normalized_prompts = [_string(prompt, "prompt") for prompt in prompts]
    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list):
        raise ModelCapacityError("phases must be a list")
    phases: list[dict[str, Any]] = []
    observed: set[str] = set()
    for index, raw_phase in enumerate(raw_phases):
        if not isinstance(raw_phase, dict):
            raise ModelCapacityError(f"phases[{index}] must be an object")
        name = _string(raw_phase.get("name"), f"phases[{index}].name")
        if name in observed:
            raise ModelCapacityError(f"duplicate phase: {name}")
        observed.add(name)
        max_tokens = _positive_int(raw_phase.get("max_tokens"), f"phases[{index}].max_tokens")
        if max_tokens < 2:
            raise ModelCapacityError("max_tokens must be at least 2 for TPOT measurement")
        phases.append(
            {
                "name": name,
                "requests": _positive_int(raw_phase.get("requests"), f"phases[{index}].requests"),
                "concurrency": _positive_int(
                    raw_phase.get("concurrency"), f"phases[{index}].concurrency"
                ),
                "max_tokens": max_tokens,
            }
        )
    missing_phases = set(REQUIRED_PHASES) - observed
    if missing_phases:
        raise ModelCapacityError(
            "model capacity phases missing: " + ", ".join(sorted(missing_phases))
        )

    raw_prometheus = payload.get("prometheus")
    if not isinstance(raw_prometheus, dict):
        raise ModelCapacityError("prometheus must be an object")
    raw_queries = raw_prometheus.get("queries")
    if not isinstance(raw_queries, dict):
        raise ModelCapacityError("prometheus.queries must be an object")
    missing_queries = set(REQUIRED_TELEMETRY) - raw_queries.keys()
    if missing_queries:
        raise ModelCapacityError(
            "prometheus queries missing: " + ", ".join(sorted(missing_queries))
        )
    nvidia = payload.get("nvidia_smi", {})
    if not isinstance(nvidia, dict):
        raise ModelCapacityError("nvidia_smi must be an object")
    enabled = nvidia.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ModelCapacityError("nvidia_smi.enabled must be boolean")
    temperature = payload.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ModelCapacityError("temperature must be a number")
    if not 0 <= float(temperature) <= 2:
        raise ModelCapacityError("temperature must be between 0 and 2")

    normalized = {
        "schema_version": 1,
        "base_url": _string(payload.get("base_url"), "base_url").rstrip("/"),
        "metrics_url": _string(payload.get("metrics_url"), "metrics_url"),
        "model": _string(payload.get("model"), "model"),
        "model_revision": _string(payload.get("model_revision"), "model_revision"),
        "quantization": _string(payload.get("quantization"), "quantization"),
        "api_key_env": _string(payload.get("api_key_env", "OPENAI_API_KEY"), "api_key_env"),
        "repetitions": _positive_int(payload.get("repetitions"), "repetitions"),
        "request_timeout_seconds": _positive_number(
            payload.get("request_timeout_seconds", 180), "request_timeout_seconds"
        ),
        "temperature": float(temperature),
        "prompts": normalized_prompts,
        "phases": phases,
        "prometheus": {
            "base_url": _string(raw_prometheus.get("base_url"), "prometheus.base_url").rstrip("/"),
            "bearer_token_env": raw_prometheus.get("bearer_token_env"),
            "step_seconds": _positive_number(
                raw_prometheus.get("step_seconds", 5), "prometheus.step_seconds"
            ),
            "queries": {
                name: _string(raw_queries[name], f"prometheus.queries.{name}")
                for name in REQUIRED_TELEMETRY
            },
        },
        "nvidia_smi": {
            "enabled": enabled,
            "interval_seconds": _positive_number(
                nvidia.get("interval_seconds", 1), "nvidia_smi.interval_seconds"
            ),
        },
    }
    bearer_env = normalized["prometheus"]["bearer_token_env"]
    if bearer_env is not None and (not isinstance(bearer_env, str) or not bearer_env):
        raise ModelCapacityError("prometheus.bearer_token_env must be a string or null")
    return normalized


async def execute_stream_request(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    api_key: str | None,
) -> ModelRequestSample:
    started = time.perf_counter()
    first_token_at: float | None = None
    completed_at: float | None = None
    output_tokens: int | None = None
    status_code: int | None = None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    continue
                usage = payload.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
                    output_tokens = int(usage["completion_tokens"])
                choices = payload.get("choices")
                if not isinstance(choices, list):
                    continue
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    content = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(content, str) and content:
                        now = time.perf_counter()
                        first_token_at = first_token_at or now
                        completed_at = now
        finished = time.perf_counter()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        return ModelRequestSample(
            success=False,
            status_code=status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
            ttft_ms=None,
            tpot_ms=None,
            output_tokens=None,
            token_count_source="missing",
            error=f"{type(error).__name__}: {error}",
        )
    ttft_ms = (first_token_at - started) * 1000 if first_token_at is not None else None
    tpot_ms = None
    if (
        first_token_at is not None
        and completed_at is not None
        and output_tokens is not None
        and output_tokens > 1
    ):
        tpot_ms = (completed_at - first_token_at) * 1000 / (output_tokens - 1)
    success = status_code == 200 and ttft_ms is not None and tpot_ms is not None
    return ModelRequestSample(
        success=success,
        status_code=status_code,
        duration_ms=(finished - started) * 1000,
        ttft_ms=ttft_ms,
        tpot_ms=tpot_ms,
        output_tokens=output_tokens,
        token_count_source="usage" if output_tokens is not None else "missing",
        error=None if success else "missing streamed content, usage, or multi-token TPOT sample",
    )


async def run_model_phase(
    client: httpx.AsyncClient,
    *,
    config: dict[str, Any],
    phase: dict[str, Any],
    api_key: str | None,
) -> tuple[list[ModelRequestSample], float]:
    semaphore = asyncio.Semaphore(phase["concurrency"])

    async def bounded(index: int) -> ModelRequestSample:
        async with semaphore:
            return await execute_stream_request(
                client,
                model=config["model"],
                prompt=config["prompts"][index % len(config["prompts"])],
                max_tokens=phase["max_tokens"],
                temperature=config["temperature"],
                api_key=api_key,
            )

    started = time.perf_counter()
    samples = await asyncio.gather(*(bounded(index) for index in range(phase["requests"])))
    return list(samples), time.perf_counter() - started


def _numeric_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "sample_count": len(values),
        "min": min(values) if values else None,
        "average": sum(values) / len(values) if values else None,
        "p95": nearest_rank_percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _prometheus_values(payload: object) -> list[float]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ModelCapacityError("Prometheus response must have status=success")
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise ModelCapacityError("Prometheus result must be a list")
    values: list[float] = []
    for series in result:
        if not isinstance(series, dict):
            raise ModelCapacityError("Prometheus result series must be an object")
        metric = series.get("metric", {})
        if not isinstance(metric, dict):
            raise ModelCapacityError("Prometheus labels must be an object")
        forbidden = FORBIDDEN_LABELS & {str(key).lower() for key in metric}
        if forbidden:
            raise ModelCapacityError(
                "Prometheus result contains forbidden labels: " + ", ".join(sorted(forbidden))
            )
        samples: list[object] = []
        if isinstance(series.get("values"), list):
            samples.extend(series["values"])
        elif isinstance(series.get("value"), list):
            samples.append(series["value"])
        for sample in samples:
            if not isinstance(sample, list) or len(sample) != 2:
                continue
            try:
                number = float(sample[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values.append(number)
    return values


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ModelCapacityError("model capacity output must stay inside the repository") from error


def _artifact(root: Path, path: Path, kind: str) -> dict[str, str]:
    return {
        "path": _relative(root, path),
        "kind": kind,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


async def _query_prometheus(
    *,
    config: dict[str, Any],
    started_at: str,
    completed_at: str,
    output_dir: Path,
) -> tuple[dict[str, list[float]], list[Path], list[str]]:
    prometheus = config["prometheus"]
    bearer_env = prometheus["bearer_token_env"]
    token = os.environ.get(bearer_env) if isinstance(bearer_env, str) else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    observations: dict[str, list[float]] = {}
    paths: list[Path] = []
    errors: list[str] = []
    async with httpx.AsyncClient(
        base_url=prometheus["base_url"], headers=headers, timeout=30, trust_env=False
    ) as client:
        for name, query in prometheus["queries"].items():
            path = output_dir / f"{name}.json"
            try:
                response = await client.get(
                    "/api/v1/query_range",
                    params={
                        "query": query,
                        "start": started_at,
                        "end": completed_at,
                        "step": prometheus["step_seconds"],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                values = _prometheus_values(payload)
            except (httpx.HTTPError, json.JSONDecodeError, ModelCapacityError) as error:
                payload = {
                    "status": "error",
                    "telemetry": name,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                values = []
                errors.append(f"{name}: {type(error).__name__}: {error}")
            _write_json(path, payload)
            observations[name] = values
            paths.append(path)
    return observations, paths, errors


async def _fetch_vllm_metrics(
    *, config: dict[str, Any], output_path: Path
) -> tuple[Path, str | None]:
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.get(config["metrics_url"])
            response.raise_for_status()
            text = response.text
        lowered = text.lower()
        leaked = sorted(label for label in FORBIDDEN_LABELS if label in lowered)
        if leaked:
            raise ModelCapacityError("vLLM metrics contain forbidden labels: " + ", ".join(leaked))
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_text, text.rstrip("\n") + "\n", encoding="utf-8")
        return output_path, None
    except (httpx.HTTPError, OSError, ModelCapacityError) as error:
        _write_json(
            output_path.with_suffix(".error.json"),
            {"status": "error", "error_type": type(error).__name__, "error": str(error)},
        )
        return output_path.with_suffix(".error.json"), f"{type(error).__name__}: {error}"


def _parse_nvidia_output(stdout: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in csv.reader(line for line in stdout.splitlines() if line.strip()):
        if len(row) != 8:
            continue
        try:
            utilization = float(row[5].strip())
            memory_used = float(row[6].strip())
            memory_total = float(row[7].strip())
        except ValueError:
            continue
        samples.append(
            {
                "timestamp": row[0].strip(),
                "index": row[1].strip(),
                "uuid_sha256": hashlib.sha256(row[2].strip().encode()).hexdigest(),
                "name": row[3].strip(),
                "driver_version": row[4].strip(),
                "utilization_percent": utilization,
                "memory_used_mib": memory_used,
                "memory_total_mib": memory_total,
            }
        )
    return samples


async def _sample_nvidia_smi(
    *, stop: asyncio.Event, interval_seconds: float
) -> tuple[list[dict[str, Any]], list[str]]:
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    command = [
        "nvidia-smi",
        "--query-gpu=timestamp,index,uuid,name,driver_version,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    while True:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = _parse_nvidia_output(result.stdout)
            if not parsed:
                errors.append("nvidia-smi returned no parseable GPU samples")
            samples.extend(parsed)
        except (OSError, subprocess.CalledProcessError) as error:
            errors.append(f"{type(error).__name__}: {error}")
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
    return samples, errors


def _headroom(telemetry: dict[str, dict[str, float | int | None]]) -> float:
    values = [
        float(value)
        for name in ("gpu", "gpu_memory", "kv_cache")
        if isinstance((value := telemetry[name].get("p95")), (int, float))
    ]
    return round(max(0.0, 100.0 - max(values)), 6) if values else 0.0


def _bottleneck(
    telemetry: dict[str, dict[str, float | int | None]], *, failed_requests: int
) -> str:
    if failed_requests:
        return "Streaming request failures or incomplete usage records were observed."
    candidates = {
        name: float(value)
        for name in ("gpu", "gpu_memory", "kv_cache")
        if isinstance((value := telemetry[name].get("p95")), (int, float))
    }
    if not candidates:
        return "No GPU or vLLM saturation telemetry was measured."
    name, value = max(candidates.items(), key=lambda item: item[1])
    return f"Highest measured saturation was {name} p95 at {value:.3f}."


async def run_model_capacity(
    *,
    root: Path,
    config: dict[str, Any],
    output_dir: Path,
    external_execution: bool,
    provider: str | None,
    region: str | None,
    cluster: str | None,
    image_digest: str | None,
    operator: str,
) -> dict[str, Any]:
    if external_execution:
        for label, value in (("provider", provider), ("region", region), ("cluster", cluster)):
            _string(value, label)
        if image_digest is None or DIGEST_PATTERN.fullmatch(image_digest) is None:
            raise ModelCapacityError("external execution requires an immutable image digest")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    _relative(root, output_dir)
    config_path = output_dir / "model-capacity-config.json"
    _write_json(config_path, config)
    artifacts: list[tuple[Path, str]] = [(config_path, "model-capacity-config")]
    commands: list[str] = []
    started_at = datetime.now(UTC)
    api_key = os.environ.get(config["api_key_env"])
    all_samples: list[ModelRequestSample] = []
    total_phase_seconds = 0.0
    telemetry_values: dict[str, list[float]] = {name: [] for name in REQUIRED_TELEMETRY}
    telemetry_errors: list[str] = []
    nvidia_samples: list[dict[str, Any]] = []
    run_records: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        base_url=config["base_url"],
        timeout=httpx.Timeout(config["request_timeout_seconds"]),
        trust_env=False,
    ) as client:
        for repetition in range(1, config["repetitions"] + 1):
            for phase in config["phases"]:
                run_name = f"r{repetition:02d}-{phase['name']}"
                commands.append(
                    f"{run_name}: model={config['model']} requests={phase['requests']} "
                    f"concurrency={phase['concurrency']} max_tokens={phase['max_tokens']}"
                )
                before_metrics, before_error = await _fetch_vllm_metrics(
                    config=config,
                    output_path=output_dir / "vllm-metrics" / f"{run_name}-before.prom",
                )
                artifacts.append((before_metrics, "vllm-metrics-snapshot"))
                if before_error:
                    telemetry_errors.append(f"{run_name} before metrics: {before_error}")
                stop = asyncio.Event()
                nvidia_task: asyncio.Task[tuple[list[dict[str, Any]], list[str]]] | None = None
                if config["nvidia_smi"]["enabled"]:
                    nvidia_task = asyncio.create_task(
                        _sample_nvidia_smi(
                            stop=stop,
                            interval_seconds=config["nvidia_smi"]["interval_seconds"],
                        )
                    )
                phase_started = datetime.now(UTC)
                try:
                    samples, phase_seconds = await run_model_phase(
                        client, config=config, phase=phase, api_key=api_key
                    )
                finally:
                    stop.set()
                    if nvidia_task is not None:
                        measured, errors = await nvidia_task
                        nvidia_samples.extend(measured)
                        telemetry_errors.extend(
                            f"{run_name} nvidia-smi: {error}" for error in errors
                        )
                phase_completed = datetime.now(UTC)
                total_phase_seconds += phase_seconds
                all_samples.extend(samples)
                sample_path = output_dir / "samples" / f"{run_name}.json"
                _write_json(
                    sample_path,
                    {
                        "schema_version": 1,
                        "phase": phase["name"],
                        "repetition": repetition,
                        "samples": [asdict(sample) for sample in samples],
                    },
                )
                artifacts.append((sample_path, "model-stream-samples"))
                after_metrics, after_error = await _fetch_vllm_metrics(
                    config=config,
                    output_path=output_dir / "vllm-metrics" / f"{run_name}-after.prom",
                )
                artifacts.append((after_metrics, "vllm-metrics-snapshot"))
                if after_error:
                    telemetry_errors.append(f"{run_name} after metrics: {after_error}")
                observations, paths, errors = await _query_prometheus(
                    config=config,
                    started_at=phase_started.isoformat(),
                    completed_at=phase_completed.isoformat(),
                    output_dir=output_dir / "prometheus" / run_name,
                )
                for name, values in observations.items():
                    telemetry_values[name].extend(values)
                artifacts.extend((path, "prometheus-query-range") for path in paths)
                telemetry_errors.extend(f"{run_name} {error}" for error in errors)
                run_records.append(
                    {
                        "name": run_name,
                        "phase": phase["name"],
                        "repetition": repetition,
                        "requests": len(samples),
                        "failed_requests": sum(not sample.success for sample in samples),
                        "duration_seconds": phase_seconds,
                    }
                )

    nvidia_path = output_dir / "nvidia-smi.json"
    _write_json(nvidia_path, {"schema_version": 1, "samples": nvidia_samples})
    artifacts.append((nvidia_path, "nvidia-smi-samples"))
    telemetry = {name: _numeric_summary(telemetry_values[name]) for name in REQUIRED_TELEMETRY}
    successful = [sample for sample in all_samples if sample.success]
    failed_requests = len(all_samples) - len(successful)
    ttft = [float(sample.ttft_ms) for sample in successful if sample.ttft_ms is not None]
    tpot = [float(sample.tpot_ms) for sample in successful if sample.tpot_ms is not None]
    output_tokens = sum(sample.output_tokens or 0 for sample in successful)
    telemetry_complete = all(
        int(telemetry[name]["sample_count"] or 0) > 0 for name in REQUIRED_TELEMETRY
    )
    exact_usage_complete = len(successful) == len(all_samples) and all(
        sample.token_count_source == "usage" and sample.output_tokens is not None
        for sample in successful
    )
    if external_execution:
        status = (
            "passed"
            if config["repetitions"] >= 2
            and failed_requests == 0
            and telemetry_complete
            and exact_usage_complete
            and bool(nvidia_samples)
            and not telemetry_errors
            else "failed"
        )
    else:
        status = "blocked_external"
    execution_path = output_dir / "execution-manifest.json"
    _write_json(
        execution_path,
        {
            "schema_version": 1,
            "external_execution": external_execution,
            "runs": run_records,
            "telemetry_errors": telemetry_errors,
            "nvidia_sample_count": len(nvidia_samples),
            "exact_usage_complete": exact_usage_complete,
        },
    )
    artifacts.append((execution_path, "model-capacity-execution-manifest"))
    git_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    completed_at = datetime.now(UTC)
    ttft_summary = build_percentile_summary(ttft)
    tpot_summary = build_percentile_summary(tpot)
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "capacity",
        "capacity_profile": "model",
        "evidence_id": f"model-capacity-{completed_at.strftime('%Y%m%dT%H%M%SZ')}",
        "milestone": "M7",
        "requirement_ids": ["M7-R7", "DR-9"],
        "status": status,
        "environment": {
            "name": cluster if external_execution else "local-or-unverified-model-target",
            "external_execution": external_execution,
            "provider": provider,
            "region": region,
            "cluster": cluster,
        },
        "commit_sha": git_result.stdout.strip(),
        "image_digest": image_digest,
        "operator": operator,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "command_or_procedure": commands,
        "workload": {
            "model": config["model"],
            "model_revision": config["model_revision"],
            "quantization": config["quantization"],
            "phases": [phase["name"] for phase in config["phases"]],
            "repetitions": config["repetitions"],
        },
        "measurements": {
            "ttft_p50_ms": float(ttft_summary["p50_ms"] or 0.0),
            "ttft_p95_ms": float(ttft_summary["p95_ms"] or 0.0),
            "ttft_p99_ms": float(ttft_summary["p99_ms"] or 0.0),
            "tpot_p50_ms": float(tpot_summary["p50_ms"] or 0.0),
            "tpot_p95_ms": float(tpot_summary["p95_ms"] or 0.0),
            "tpot_p99_ms": float(tpot_summary["p99_ms"] or 0.0),
            "tokens_per_second": output_tokens / total_phase_seconds
            if total_phase_seconds > 0
            else 0.0,
            "error_rate": failed_requests / len(all_samples) if all_samples else 1.0,
            "headroom_percent": _headroom(telemetry),
            "bottleneck": _bottleneck(telemetry, failed_requests=failed_requests),
            "telemetry": telemetry,
            "request_count": len(all_samples),
            "output_tokens": output_tokens,
            "nvidia_sample_count": len(nvidia_samples),
        },
        "artifacts": [_artifact(root, path, kind) for path, kind in artifacts],
        "limitations": [
            "TTFT starts before the HTTP request and includes network and queue time.",
            "TPOT uses exact streamed completion_tokens usage and requires at least two tokens.",
            "The report only claims the pinned model revision, quantization, image, and target.",
        ],
        "owner": "model-platform-engineering",
    }
    if status == "blocked_external":
        report["blocking_reason"] = (
            "No confirmed external GPU/vLLM environment with immutable image and fixed model "
            "revision was declared."
        )
        report["prerequisites"] = [
            "Provision an isolated GPU target with a pinned vLLM image and model revision.",
            "Expose aggregate Prometheus GPU, GPU memory, KV cache, and queue queries.",
            "Run at least two repetitions with nvidia-smi and vLLM metrics available.",
        ]
    validate_evidence(report, root=root)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure OpenAI-compatible streaming TTFT/TPOT and GPU/vLLM capacity"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/model-capacity"))
    parser.add_argument("--report-path", type=Path, default=Path("tmp/model-capacity.json"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--external-execution", action="store_true")
    parser.add_argument("--confirm-external", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--region")
    parser.add_argument("--cluster")
    parser.add_argument("--image-digest")
    parser.add_argument(
        "--operator", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator"
    )
    args = parser.parse_args()
    try:
        config = load_model_capacity_config(args.config)
        if args.image_digest is not None and DIGEST_PATTERN.fullmatch(args.image_digest) is None:
            raise ModelCapacityError("--image-digest must be sha256:<64 lowercase hex>")
        if not args.execute:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "model": config["model"],
                        "model_revision": config["model_revision"],
                        "phase_count": len(config["phases"]),
                        "repetitions": config["repetitions"],
                        "external_execution": args.external_execution,
                    },
                    indent=2,
                )
            )
            return
        if args.external_execution and not args.confirm_external:
            raise ModelCapacityError(
                "--external-execution requires --confirm-external before generating GPU load"
            )
        report = asyncio.run(
            run_model_capacity(
                root=args.root,
                config=config,
                output_dir=args.output_dir,
                external_execution=args.external_execution,
                provider=args.provider,
                region=args.region,
                cluster=args.cluster,
                image_digest=args.image_digest,
                operator=args.operator,
            )
        )
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.report_path, report)
    except (ModelCapacityError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": report["status"], "report": args.report_path.as_posix()}))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
