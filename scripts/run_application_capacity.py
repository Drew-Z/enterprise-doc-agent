from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from enterprise_doc_core.evaluation import build_percentile_summary, nearest_rank_percentile

try:
    from scripts.load_m5 import execute_load
    from scripts.validate_recovery_capacity_evidence import validate_evidence
except ModuleNotFoundError:
    from load_m5 import execute_load
    from validate_recovery_capacity_evidence import validate_evidence

REQUIRED_PHASES = ("ramp", "steady_state", "burst", "recovery")
REQUIRED_TELEMETRY = (
    "cpu",
    "memory",
    "database_pool",
    "queue",
    "redis",
    "object_store",
    "model",
)
SCENARIOS = {
    "health",
    "ready",
    "status",
    "agent-create",
    "duplicate-agent-create",
    "end-to-end",
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_PROMETHEUS_LABELS = {
    "actor_id",
    "document_id",
    "document_version_id",
    "run_id",
    "session_id",
    "tenant_id",
    "user_id",
}


class ApplicationCapacityError(ValueError):
    """Raised when a capacity plan or measured result is unsafe or incomplete."""


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ApplicationCapacityError(f"{label} must be a positive integer")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ApplicationCapacityError(f"{label} must be positive")
    return float(value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationCapacityError(f"{label} must be a non-empty string")
    return value.strip()


def load_capacity_config(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ApplicationCapacityError(f"cannot read capacity config: {error}") from error
    return validate_capacity_config(payload)


def validate_capacity_config(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApplicationCapacityError("capacity config must be an object")
    if payload.get("schema_version") != 1:
        raise ApplicationCapacityError("schema_version must be 1")
    scenario = _string(payload.get("scenario"), "scenario")
    if scenario not in SCENARIOS:
        raise ApplicationCapacityError(f"unsupported scenario: {scenario}")
    base_url = _string(payload.get("base_url"), "base_url")
    repetitions = _positive_int(payload.get("repetitions"), "repetitions")
    target_p95_ms = _positive_number(payload.get("target_p95_ms"), "target_p95_ms")
    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list):
        raise ApplicationCapacityError("phases must be a list")
    phases: list[dict[str, Any]] = []
    observed_names: set[str] = set()
    for index, raw_phase in enumerate(raw_phases):
        if not isinstance(raw_phase, dict):
            raise ApplicationCapacityError(f"phases[{index}] must be an object")
        name = _string(raw_phase.get("name"), f"phases[{index}].name")
        if name in observed_names:
            raise ApplicationCapacityError(f"duplicate phase: {name}")
        observed_names.add(name)
        phase_scenario = raw_phase.get("scenario", scenario)
        if not isinstance(phase_scenario, str) or phase_scenario not in SCENARIOS:
            raise ApplicationCapacityError(f"unsupported phase scenario: {phase_scenario}")
        phases.append(
            {
                "name": name,
                "scenario": phase_scenario,
                "requests": _positive_int(raw_phase.get("requests"), f"phases[{index}].requests"),
                "concurrency": _positive_int(
                    raw_phase.get("concurrency"), f"phases[{index}].concurrency"
                ),
            }
        )
    missing_phases = set(REQUIRED_PHASES) - observed_names
    if missing_phases:
        raise ApplicationCapacityError(
            "capacity phases missing: " + ", ".join(sorted(missing_phases))
        )

    raw_prometheus = payload.get("prometheus")
    if not isinstance(raw_prometheus, dict):
        raise ApplicationCapacityError("prometheus must be an object")
    raw_queries = raw_prometheus.get("queries")
    if not isinstance(raw_queries, dict):
        raise ApplicationCapacityError("prometheus.queries must be an object")
    missing_queries = set(REQUIRED_TELEMETRY) - raw_queries.keys()
    if missing_queries:
        raise ApplicationCapacityError(
            "prometheus queries missing: " + ", ".join(sorted(missing_queries))
        )
    queries = {
        name: _string(raw_queries[name], f"prometheus.queries.{name}")
        for name in REQUIRED_TELEMETRY
    }
    step_seconds = _positive_number(
        raw_prometheus.get("step_seconds", 5), "prometheus.step_seconds"
    )

    normalized = {
        "schema_version": 1,
        "scenario": scenario,
        "base_url": base_url.rstrip("/"),
        "repetitions": repetitions,
        "target_p95_ms": target_p95_ms,
        "token_env": str(payload.get("token_env", "ENTERPRISE_DOC_LOAD_TOKEN")),
        "document_version_id": payload.get("document_version_id"),
        "run_id": payload.get("run_id"),
        "request_timeout_seconds": _positive_number(
            payload.get("request_timeout_seconds", 30), "request_timeout_seconds"
        ),
        "terminal_timeout_seconds": _positive_number(
            payload.get("terminal_timeout_seconds", 120), "terminal_timeout_seconds"
        ),
        "poll_seconds": _positive_number(payload.get("poll_seconds", 0.25), "poll_seconds"),
        "resource_sample_interval_seconds": _positive_number(
            payload.get("resource_sample_interval_seconds", 0.25),
            "resource_sample_interval_seconds",
        ),
        "phases": phases,
        "prometheus": {
            "base_url": _string(raw_prometheus.get("base_url"), "prometheus.base_url").rstrip("/"),
            "bearer_token_env": raw_prometheus.get("bearer_token_env"),
            "step_seconds": step_seconds,
            "queries": queries,
        },
    }
    if not isinstance(normalized["token_env"], str) or not normalized["token_env"]:
        raise ApplicationCapacityError("token_env must be a non-empty string")
    bearer_env = normalized["prometheus"]["bearer_token_env"]
    if bearer_env is not None and (not isinstance(bearer_env, str) or not bearer_env):
        raise ApplicationCapacityError("prometheus.bearer_token_env must be a string or null")
    return normalized


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
        raise ApplicationCapacityError("Prometheus response must have status=success")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ApplicationCapacityError("Prometheus response data must be an object")
    result = data.get("result")
    if not isinstance(result, list):
        raise ApplicationCapacityError("Prometheus response result must be a list")
    values: list[float] = []
    for series in result:
        if not isinstance(series, dict):
            raise ApplicationCapacityError("Prometheus result series must be an object")
        metric = series.get("metric", {})
        if not isinstance(metric, dict):
            raise ApplicationCapacityError("Prometheus metric labels must be an object")
        forbidden = FORBIDDEN_PROMETHEUS_LABELS & {str(key).lower() for key in metric}
        if forbidden:
            raise ApplicationCapacityError(
                "Prometheus result contains forbidden high-cardinality labels: "
                + ", ".join(sorted(forbidden))
            )
        raw_samples: list[object] = []
        if isinstance(series.get("values"), list):
            raw_samples.extend(series["values"])
        elif isinstance(series.get("value"), list):
            raw_samples.append(series["value"])
        for sample in raw_samples:
            if not isinstance(sample, list) or len(sample) != 2:
                continue
            try:
                number = float(sample[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values.append(number)
    return values


def summarize_prometheus_payload(payload: object) -> dict[str, float | int | None]:
    return _numeric_summary(_prometheus_values(payload))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ApplicationCapacityError("capacity output must stay inside the repository") from error


def _artifact(root: Path, path: Path, kind: str) -> dict[str, str]:
    return {"path": _relative(root, path), "kind": kind, "sha256": _sha256(path)}


def _load_args(config: dict[str, Any], phase: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        scenario=phase["scenario"],
        base_url=config["base_url"],
        requests=phase["requests"],
        concurrency=phase["concurrency"],
        token_env=config["token_env"],
        document_version_id=config["document_version_id"],
        run_id=config["run_id"],
        request_timeout_seconds=config["request_timeout_seconds"],
        terminal_timeout_seconds=config["terminal_timeout_seconds"],
        poll_seconds=config["poll_seconds"],
        target_p95_ms=config["target_p95_ms"],
        sample_resources=True,
        resource_process_id=None,
        resource_sample_interval_seconds=config["resource_sample_interval_seconds"],
    )


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
        base_url=prometheus["base_url"], timeout=30, trust_env=False, headers=headers
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
            except (httpx.HTTPError, json.JSONDecodeError, ApplicationCapacityError) as error:
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


def _merge_telemetry(
    observations: dict[str, list[float]],
) -> dict[str, dict[str, float | int | None]]:
    return {name: _numeric_summary(observations.get(name, [])) for name in REQUIRED_TELEMETRY}


def _headroom(telemetry: dict[str, dict[str, float | int | None]]) -> float:
    saturation = [
        float(value)
        for name in ("cpu", "memory", "database_pool")
        if isinstance((value := telemetry[name].get("p95")), (int, float))
    ]
    return round(max(0.0, 100.0 - max(saturation)), 6) if saturation else 0.0


def _bottleneck(
    telemetry: dict[str, dict[str, float | int | None]], *, failed_requests: int
) -> str:
    if failed_requests:
        return "Request failures or terminal timeouts were observed."
    candidates = {
        name: float(value)
        for name in ("cpu", "memory", "database_pool")
        if isinstance((value := telemetry[name].get("p95")), (int, float))
    }
    if not candidates:
        return "No dependency telemetry was measured; capacity bottleneck is unknown."
    name, value = max(candidates.items(), key=lambda item: item[1])
    return f"Highest measured saturation was {name} p95 at {value:.3f}."


def _parse_image_digests(values: list[str]) -> dict[str, str] | None:
    if not values:
        return None
    parsed: dict[str, str] = {}
    for value in values:
        service, separator, digest = value.partition("=")
        if not separator or not service or DIGEST_PATTERN.fullmatch(digest) is None:
            raise ApplicationCapacityError(
                "--image-digest must use service=sha256:<64 lowercase hex>"
            )
        if service in parsed:
            raise ApplicationCapacityError(f"duplicate image digest service: {service}")
        parsed[service] = digest
    return parsed


async def run_capacity_matrix(
    *,
    root: Path,
    config: dict[str, Any],
    output_dir: Path,
    external_execution: bool,
    provider: str | None,
    region: str | None,
    cluster: str | None,
    image_digests: dict[str, str] | None,
    operator: str,
) -> dict[str, Any]:
    if external_execution:
        for label, value in (("provider", provider), ("region", region), ("cluster", cluster)):
            _string(value, label)
        if not image_digests:
            raise ApplicationCapacityError("external execution requires immutable image digests")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    _relative(root, output_dir)
    started_at = datetime.now(UTC)
    config_path = output_dir / "capacity-config.json"
    _write_json(config_path, config)
    artifact_paths: list[tuple[Path, str]] = [(config_path, "capacity-config")]
    commands: list[str] = []
    run_records: list[dict[str, Any]] = []
    latency_values: list[float] = []
    completed_requests = 0
    failed_requests = 0
    duration_seconds = 0.0
    telemetry_observations: dict[str, list[float]] = {name: [] for name in REQUIRED_TELEMETRY}
    telemetry_errors: list[str] = []

    for repetition in range(1, config["repetitions"] + 1):
        for phase in config["phases"]:
            run_name = f"r{repetition:02d}-{phase['name']}"
            commands.append(
                f"{run_name}: {phase['scenario']} requests={phase['requests']} "
                f"concurrency={phase['concurrency']}"
            )
            load_report, samples = await execute_load(_load_args(config, phase))
            report_path = output_dir / "runs" / f"{run_name}.json"
            _write_json(report_path, load_report.model_dump(mode="json"))
            artifact_paths.append((report_path, "bounded-load-report"))
            sample_path = output_dir / "samples" / f"{run_name}.json"
            _write_json(
                sample_path,
                {
                    "schema_version": 1,
                    "phase": phase["name"],
                    "repetition": repetition,
                    "samples": [
                        {
                            "duration_ms": sample.duration_ms,
                            "success": sample.success,
                            "status_code": sample.status_code,
                            "terminal_status": sample.terminal_status,
                        }
                        for sample in samples
                    ],
                },
            )
            artifact_paths.append((sample_path, "request-samples"))
            latency_values.extend(sample.duration_ms for sample in samples)
            completed_requests += len(samples)
            failed_requests += sum(not sample.success for sample in samples)
            duration_seconds += load_report.duration_seconds

            observations, snapshot_paths, errors = await _query_prometheus(
                config=config,
                started_at=load_report.started_at,
                completed_at=load_report.completed_at,
                output_dir=output_dir / "prometheus" / run_name,
            )
            for name, values in observations.items():
                telemetry_observations[name].extend(values)
            artifact_paths.extend((path, "prometheus-query-range") for path in snapshot_paths)
            telemetry_errors.extend(f"{run_name} {error}" for error in errors)
            run_records.append(
                {
                    "name": run_name,
                    "phase": phase["name"],
                    "repetition": repetition,
                    "load_status": load_report.status,
                    "completed_requests": len(samples),
                    "failed_requests": sum(not sample.success for sample in samples),
                    "telemetry_errors": errors,
                }
            )

    telemetry = _merge_telemetry(telemetry_observations)
    all_loads_passed = all(record["load_status"] == "passed" for record in run_records)
    telemetry_complete = all(
        isinstance(telemetry[name].get("sample_count"), int)
        and int(telemetry[name]["sample_count"]) > 0
        for name in REQUIRED_TELEMETRY
    )
    if external_execution:
        status = (
            "passed"
            if all_loads_passed
            and telemetry_complete
            and not telemetry_errors
            and config["repetitions"] >= 2
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
            "all_loads_passed": all_loads_passed,
            "telemetry_complete": telemetry_complete,
        },
    )
    artifact_paths.append((execution_path, "capacity-execution-manifest"))
    completed_at = datetime.now(UTC)
    git_result = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    commit_sha = git_result.stdout.strip()
    latency = build_percentile_summary(latency_values)
    throughput = completed_requests / duration_seconds if duration_seconds > 0 else 0.0
    measurements = {
        "p50_ms": float(latency["p50_ms"] or 0.0),
        "p95_ms": float(latency["p95_ms"] or 0.0),
        "p99_ms": float(latency["p99_ms"] or 0.0),
        "error_rate": failed_requests / completed_requests if completed_requests else 1.0,
        "throughput_per_second": throughput,
        "headroom_percent": _headroom(telemetry),
        "bottleneck": _bottleneck(telemetry, failed_requests=failed_requests),
        "telemetry": telemetry,
        "completed_requests": completed_requests,
        "run_count": len(run_records),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "capacity",
        "capacity_profile": "application",
        "evidence_id": f"application-capacity-{completed_at.strftime('%Y%m%dT%H%M%SZ')}",
        "milestone": "M5",
        "requirement_ids": ["M5-R10", "DR-9"],
        "status": status,
        "environment": {
            "name": cluster if external_execution else "local-or-unverified-capacity-target",
            "external_execution": external_execution,
            "provider": provider,
            "region": region,
            "cluster": cluster,
        },
        "commit_sha": commit_sha,
        "image_digest": image_digests,
        "operator": operator,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "command_or_procedure": commands,
        "workload": {
            "scenario": config["scenario"],
            "phases": [phase["name"] for phase in config["phases"]],
            "repetitions": config["repetitions"],
            "base_url": config["base_url"],
        },
        "measurements": measurements,
        "artifacts": [_artifact(root, path, kind) for path, kind in artifact_paths],
        "limitations": [
            "The runner only claims the exact configured target, image digests, and time window.",
            "Prometheus queries must aggregate away tenant, user, document, run, and session IDs.",
            "Capacity approval still requires workload-owner review and production-like isolation.",
        ],
        "owner": "performance-engineering",
    }
    if status == "blocked_external":
        report["blocking_reason"] = (
            "The matrix may run locally, but no confirmed isolated production-like environment "
            "with immutable deployed images was declared."
        )
        report["prerequisites"] = [
            "Provision an isolated production-like target and managed Prometheus endpoint.",
            "Deploy and record immutable application image digests.",
            "Run at least two repetitions of every phase with representative authorized data.",
        ]
    validate_evidence(report, root=root)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run repeated application capacity phases with Prometheus evidence"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/application-capacity"))
    parser.add_argument("--report-path", type=Path, default=Path("tmp/application-capacity.json"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--external-execution", action="store_true")
    parser.add_argument("--confirm-external", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--region")
    parser.add_argument("--cluster")
    parser.add_argument("--image-digest", action="append", default=[])
    parser.add_argument(
        "--operator", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator"
    )
    args = parser.parse_args()
    try:
        config = load_capacity_config(args.config)
        image_digests = _parse_image_digests(args.image_digest)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "phase_count": len(config["phases"]),
                        "repetitions": config["repetitions"],
                        "external_execution": args.external_execution,
                    },
                    indent=2,
                )
            )
            return
        if args.external_execution and not args.confirm_external:
            raise ApplicationCapacityError(
                "--external-execution requires --confirm-external before generating load"
            )
        report = asyncio.run(
            run_capacity_matrix(
                root=args.root,
                config=config,
                output_dir=args.output_dir,
                external_execution=args.external_execution,
                provider=args.provider,
                region=args.region,
                cluster=args.cluster,
                image_digests=image_digests,
                operator=args.operator,
            )
        )
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.report_path, report)
    except (ApplicationCapacityError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": report["status"], "report": args.report_path.as_posix()}))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
