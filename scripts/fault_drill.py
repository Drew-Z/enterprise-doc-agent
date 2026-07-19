from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "infra" / "compose" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
ALLOWED_ENVIRONMENTS = {"local", "test"}
CONFIRMATION = "local-fault-drill"


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
    raise ValueError(f"unsupported scenario: {scenario}")


def validate_environment(environment: str) -> None:
    if environment not in ALLOWED_ENVIRONMENTS:
        raise FaultDrillFailure("Fault drills are restricted to local and test environments.")


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
    return {
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
            "This drill validates dependency readiness loss and recovery on local Compose only.",
            (
                "It does not prove managed-service failover, production RTO, or zero "
                "duplicate side effects."
            ),
            (
                "Redis Outbox republish is not executed by this readiness-only drill."
                if scenario == "redis"
                else (
                    "MinIO bucket/object readability is not executed by this readiness-only drill."
                )
            ),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or run guarded local dependency outage and recovery drills"
    )
    parser.add_argument("--scenario", choices=("redis", "minio", "worker-lease"), required=True)
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
    report = run_dependency_drill(
        scenario=args.scenario,
        environment=args.environment,
        readiness_url=args.readiness_url,
        timeout_seconds=args.timeout_seconds,
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
