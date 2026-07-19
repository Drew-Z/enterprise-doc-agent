from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

from pydantic import BaseModel

from enterprise_doc_core.agents import ModelTimeoutError
from enterprise_doc_core.config import FaultInjectionSettings
from enterprise_doc_core.evaluation import (
    FaultExperimentReport,
    ReportProvenance,
    capture_report_provenance,
    seal_report,
    seal_report_payload,
)
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_core.object_store.errors import ObjectStoreUnavailable
from enterprise_doc_worker.faults import (
    FaultController,
    FaultInjectingHandler,
    FaultInjectingMcpClient,
    FaultInjectingModelGateway,
    FaultInjectingMultipartObjectStore,
    InjectedRetryableHandlerError,
)
from enterprise_doc_worker.mcp_client import McpClientTimeout

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "infra" / "compose" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
ALLOWED_ENVIRONMENTS = {"local", "test"}
CONFIRMATION = "local-fault-drill"
DETERMINISTIC_SCENARIOS = {
    "handler-retryable",
    "model-timeout",
    "mcp-timeout",
    "object-store-unavailable",
}


class FaultDrillFailure(RuntimeError):
    pass


def build_plan(scenario: str) -> list[str]:
    if scenario == "redis":
        return [
            "verify API readiness before the outage",
            "stop the local Redis Compose service",
            "verify API readiness becomes unavailable",
            "restart Redis and wait for its health check",
            "verify API readiness recovers",
            "allow the Outbox publishing lease to expire before checking republish",
        ]
    if scenario == "minio":
        return [
            "verify API readiness before the outage",
            "stop the local MinIO Compose service",
            "verify API readiness becomes unavailable",
            "restart MinIO and wait for its health check",
            "verify API readiness recovers and existing buckets remain readable",
        ]
    if scenario == "worker-lease":
        return [
            "create a job with max_attempts at least two and observe attempt one running",
            "hard-kill the active consumer instead of requesting cooperative cancellation",
            "wait beyond the database lease duration",
            "start a consumer with a different worker id and observe attempt two claim",
            "verify attempt one is abandoned and stale completion is fenced",
            "verify one effective terminal side effect",
        ]
    if scenario in DETERMINISTIC_SCENARIOS:
        return [
            "enable one deterministic local/test-only fault wrapper",
            "invoke the guarded operation and verify the stable expected error code",
            "invoke the operation again and verify one-shot delegation succeeds",
            "record bounded side effects, provenance, and a canonical payload hash",
        ]
    raise ValueError(f"unsupported scenario: {scenario}")


def validate_environment(environment: str) -> None:
    if environment not in ALLOWED_ENVIRONMENTS:
        raise FaultDrillFailure("Fault drills are restricted to local and test environments.")


class _ToolResult(BaseModel):
    value: str


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=uuid4(),
        actor_id=uuid4(),
        worker_id="fault-drill",
        lease_token=uuid4(),
        fencing_token=1,
        job_type="document.ingest",
        payload={},
    )


async def run_deterministic_fault_drill(
    *,
    scenario: str,
    environment: str,
    provenance: ReportProvenance | None = None,
) -> FaultExperimentReport:
    validate_environment(environment)
    if scenario not in DETERMINISTIC_SCENARIOS:
        raise FaultDrillFailure(f"unsupported deterministic scenario: {scenario}")
    started_at = datetime.now(UTC).isoformat()
    observed_error_code: str | None = None
    delegated = False
    delegated_calls = 0

    if scenario == "handler-retryable":

        async def inner(_: ClaimedJob) -> None:
            nonlocal delegated_calls
            delegated_calls += 1

        adapter = FaultInjectingHandler(
            inner,
            FaultController(
                FaultInjectionSettings(
                    enabled=True,
                    target="handler",
                    mode="retryable",
                )
            ),
        )
        try:
            await adapter(_claim())
        except InjectedRetryableHandlerError as error:
            observed_error_code = error.code
        await adapter(_claim())
        delegated = delegated_calls == 1
        target, mode, expected_code = "handler", "retryable", "fault_injected_retryable"
    elif scenario == "model-timeout":

        class Gateway:
            async def generate(self, _: Any) -> str:
                nonlocal delegated_calls
                delegated_calls += 1
                return "delegated"

        adapter = FaultInjectingModelGateway(
            Gateway(),  # type: ignore[arg-type]
            FaultController(
                FaultInjectionSettings(
                    enabled=True,
                    target="model",
                    mode="model_timeout",
                )
            ),
        )
        try:
            await adapter.generate(object())  # type: ignore[arg-type]
        except ModelTimeoutError as error:
            observed_error_code = error.code
        delegated = await adapter.generate(object()) == "delegated"  # type: ignore[arg-type,comparison-overlap]
        target, mode, expected_code = "model", "model_timeout", "model_timeout"
    elif scenario == "mcp-timeout":

        class Client:
            async def call(self, **_: Any) -> _ToolResult:
                nonlocal delegated_calls
                delegated_calls += 1
                return _ToolResult(value="delegated")

        adapter = FaultInjectingMcpClient(
            Client(),  # type: ignore[arg-type]
            FaultController(
                FaultInjectionSettings(
                    enabled=True,
                    target="mcp",
                    mode="mcp_client_timeout",
                )
            ),
        )
        request = _ToolResult(value="request")
        try:
            await adapter.call(
                tool_name="search_document",
                request=request,
                result_model=_ToolResult,
                context_token="local-context",
            )
        except McpClientTimeout as error:
            observed_error_code = error.code
        result = await adapter.call(
            tool_name="search_document",
            request=request,
            result_model=_ToolResult,
            context_token="local-context",
        )
        delegated = result.value == "delegated" and delegated_calls == 1
        target, mode, expected_code = "mcp", "mcp_client_timeout", "mcp_client_timeout"
    else:

        class Store:
            async def get_range(self, **_: Any) -> bytes:
                nonlocal delegated_calls
                delegated_calls += 1
                return b"delegated"

        adapter = FaultInjectingMultipartObjectStore(
            Store(),  # type: ignore[arg-type]
            FaultController(
                FaultInjectionSettings(
                    enabled=True,
                    target="multipart",
                    mode="object_store_unavailable",
                )
            ),
        )
        try:
            await adapter.get_range(bucket="documents", key="local", start=0, end_inclusive=8)
        except ObjectStoreUnavailable as error:
            observed_error_code = error.code
        delegated = (
            await adapter.get_range(
                bucket="documents",
                key="local",
                start=0,
                end_inclusive=8,
            )
            == b"delegated"
            and delegated_calls == 1
        )
        target, mode, expected_code = (
            "multipart",
            "object_store_unavailable",
            "object_store_unavailable",
        )

    resolved_provenance = provenance or capture_report_provenance(
        command=["internal", f"scripts.fault_drill.{scenario}"],
        root=ROOT,
        execution_scope="local-deterministic-fault",
    )
    passed = observed_error_code == expected_code and delegated
    return seal_report(
        FaultExperimentReport(
            experiment=scenario,
            status="passed" if passed else "failed",
            injection={
                "target": target,
                "mode": mode,
                "trigger_after": 0,
                "trigger_every": 0,
            },
            expected={
                "error_code": expected_code,
                "one_shot_delegation": True,
            },
            observed={
                "error_code": observed_error_code,
                "one_shot_delegation": delegated,
            },
            side_effects={"delegated_calls": delegated_calls},
            limitations=[
                "This deterministic wrapper drill does not execute Docker, a database, or a "
                "remote provider.",
                "Integration recovery and production failure handling remain separate gates.",
            ],
            provenance=resolved_provenance,
            started_at=started_at,
            completed_at=datetime.now(UTC).isoformat(),
        )
    )


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except OSError as error:
        raise FaultDrillFailure(f"Command could not start: {' '.join(command)}") from error
    if result.returncode != 0:
        raise FaultDrillFailure(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )


def _ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ready"


def _wait_for_readiness(
    url: str,
    *,
    expected_ready: bool,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if _ready(url) is expected_ready:
            return
        sleep(0.5)
    state = "ready" if expected_ready else "unavailable"
    raise FaultDrillFailure(f"API readiness did not become {state} before the timeout.")


def run_dependency_drill(
    *,
    scenario: str,
    environment: str,
    readiness_url: str,
    timeout_seconds: float,
    provenance: ReportProvenance | None = None,
) -> dict[str, Any]:
    if scenario not in {"redis", "minio"}:
        raise FaultDrillFailure("Automated execution supports only Redis and MinIO drills.")
    service = scenario
    started_at = datetime.now(UTC)
    started = time.monotonic()
    service_needs_recovery = False
    recovered = False
    recovery_seconds: float | None = None
    failure: str | None = None
    observed_steps: list[dict[str, str]] = []
    cleanup: dict[str, Any] = {
        "attempted": False,
        "restart_succeeded": None,
        "readiness_succeeded": None,
        "error": None,
    }

    def run_step(name: str, action: Callable[[], None]) -> None:
        try:
            action()
        except FaultDrillFailure as error:
            observed_steps.append({"name": name, "status": "failed", "detail": str(error)})
            raise
        observed_steps.append({"name": name, "status": "passed"})

    try:
        run_step(
            "pre_outage_readiness",
            lambda: _wait_for_readiness(
                readiness_url,
                expected_ready=True,
                timeout_seconds=timeout_seconds,
            ),
        )
        run_step("stop_dependency", lambda: _run([*COMPOSE, "stop", service]))
        service_needs_recovery = True
        run_step(
            "outage_readiness",
            lambda: _wait_for_readiness(
                readiness_url,
                expected_ready=False,
                timeout_seconds=timeout_seconds,
            ),
        )
        run_step(
            "restart_dependency",
            lambda: _run([*COMPOSE, "up", "-d", "--wait", service]),
        )
        recovery_started = time.monotonic()
        run_step(
            "recovered_readiness",
            lambda: _wait_for_readiness(
                readiness_url,
                expected_ready=True,
                timeout_seconds=timeout_seconds,
            ),
        )
        recovery_seconds = time.monotonic() - recovery_started
        recovered = True
        service_needs_recovery = False
    except FaultDrillFailure as error:
        failure = str(error)
    finally:
        if service_needs_recovery:
            cleanup["attempted"] = True
            cleanup_started = time.monotonic()
            try:
                _run([*COMPOSE, "up", "-d", "--wait", service])
                cleanup["restart_succeeded"] = True
                _wait_for_readiness(
                    readiness_url,
                    expected_ready=True,
                    timeout_seconds=timeout_seconds,
                )
                cleanup["readiness_succeeded"] = True
                recovered = True
                if recovery_seconds is None:
                    recovery_seconds = time.monotonic() - cleanup_started
            except FaultDrillFailure as error:
                cleanup["restart_succeeded"] = cleanup["restart_succeeded"] is True
                cleanup["readiness_succeeded"] = False
                cleanup["error"] = str(error)
    completed_at = datetime.now(UTC)
    passed = failure is None and cleanup["error"] is None and recovered
    resolved_provenance = provenance or capture_report_provenance(
        command=["internal", f"scripts.fault_drill.{scenario}"],
        root=ROOT,
        execution_scope="local-compose-fault",
    )
    return seal_report_payload(
        {
            "schema_version": 1,
            "scenario": f"{scenario}-outage-recovery",
            "status": "passed" if passed else "failed",
            "environment": environment,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": time.monotonic() - started,
            "recovery_seconds": recovery_seconds,
            "plan": build_plan(scenario),
            "steps": observed_steps,
            "failure": failure,
            "cleanup": cleanup,
            "limitations": [
                "This drill validates dependency readiness loss and recovery on local "
                "Compose only.",
                (
                    "It does not prove managed-service failover, production RTO, or zero "
                    "duplicate side effects."
                ),
                (
                    "Redis Outbox republish is not executed by this readiness-only drill."
                    if scenario == "redis"
                    else (
                        "MinIO bucket/object readability is not executed by this "
                        "readiness-only drill."
                    )
                ),
            ],
            "owner": "platform-engineering",
            "provenance": resolved_provenance.model_dump(mode="json"),
        }
    )


def _unexpected_failure_code(error: Exception) -> str:
    if isinstance(error, json.JSONDecodeError):
        return "fault_drill_invalid_json"
    if isinstance(error, (HTTPError, URLError, TimeoutError)):
        return "fault_drill_dependency_unavailable"
    if isinstance(error, OSError):
        return "fault_drill_os_error"
    if isinstance(error, FaultDrillFailure):
        return "fault_drill_failure"
    return "fault_drill_unexpected_error"


def _unexpected_failure_report(
    *,
    scenario: str,
    environment: str,
    provenance: ReportProvenance,
    started_at: str,
    error: Exception,
) -> dict[str, Any]:
    error_code = _unexpected_failure_code(error)
    completed_at = datetime.now(UTC).isoformat()
    limitation = (
        "The drill stopped unexpectedly; only the stable error category is recorded and "
        "no recovery claim is made."
    )
    if scenario in DETERMINISTIC_SCENARIOS:
        return seal_report(
            FaultExperimentReport(
                experiment=scenario,
                status="failed",
                injection={},
                expected={},
                observed={
                    "error_code": error_code,
                    "one_shot_delegation": False,
                },
                side_effects={"delegated_calls": 0},
                limitations=[limitation],
                provenance=provenance,
                started_at=started_at,
                completed_at=completed_at,
            )
        ).model_dump(mode="json")
    return seal_report_payload(
        {
            "schema_version": 1,
            "scenario": f"{scenario}-outage-recovery",
            "status": "failed",
            "environment": environment,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": 0.0,
            "recovery_seconds": None,
            "plan": build_plan(scenario),
            "steps": [],
            "failure": error_code,
            "cleanup": {
                "attempted": False,
                "restart_succeeded": None,
                "readiness_succeeded": None,
                "error": None,
            },
            "limitations": [limitation],
            "owner": "platform-engineering",
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def _report_command(args: argparse.Namespace) -> list[str]:
    command = [
        "python",
        "scripts/fault_drill.py",
        "--scenario",
        str(args.scenario),
        "--run",
        "--environment",
        str(args.environment),
        "--confirm",
        CONFIRMATION,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.scenario not in DETERMINISTIC_SCENARIOS:
        command.extend(["--readiness-url", "<readiness-url>"])
    if args.report_path is not None:
        command.extend(["--report-path", "<report-path>"])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or run guarded local dependency outage and recovery drills"
    )
    parser.add_argument(
        "--scenario",
        choices=("redis", "minio", "worker-lease", *sorted(DETERMINISTIC_SCENARIOS)),
        required=True,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--environment", default=os.environ.get("APP_ENV", "local"))
    parser.add_argument("--confirm")
    parser.add_argument("--readiness-url", default="http://127.0.0.1:8000/health/ready")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()

    validate_environment(args.environment)
    if args.plan:
        print(json.dumps({"scenario": args.scenario, "steps": build_plan(args.scenario)}, indent=2))
        return
    if args.confirm != CONFIRMATION:
        raise SystemExit(f"--run requires --confirm {CONFIRMATION}")
    if args.timeout_seconds <= 0:
        raise SystemExit("timeout must be positive")
    if args.scenario == "worker-lease":
        raise SystemExit(
            "worker-lease is an operator drill: use --plan and record the job/attempt evidence; "
            "the script will not kill an unspecified process"
        )
    provenance = capture_report_provenance(
        command=_report_command(args),
        root=ROOT,
        execution_scope=(
            "local-deterministic-fault"
            if args.scenario in DETERMINISTIC_SCENARIOS
            else "local-compose-fault"
        ),
    )
    started_at = datetime.now(UTC).isoformat()
    try:
        if args.scenario in DETERMINISTIC_SCENARIOS:
            model_report = asyncio.run(
                run_deterministic_fault_drill(
                    scenario=args.scenario,
                    environment=args.environment,
                    provenance=provenance,
                )
            )
            report = model_report.model_dump(mode="json")
        else:
            report = run_dependency_drill(
                scenario=args.scenario,
                environment=args.environment,
                readiness_url=args.readiness_url,
                timeout_seconds=args.timeout_seconds,
                provenance=provenance,
            )
    except Exception as error:
        report = _unexpected_failure_report(
            scenario=args.scenario,
            environment=args.environment,
            provenance=provenance,
            started_at=started_at,
            error=error,
        )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
