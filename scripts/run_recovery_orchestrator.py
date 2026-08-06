from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONFIRMATION = "run-recovery-drill"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SENSITIVE_ARGUMENT_PATTERNS = (
    re.compile(r"(?i)(?:password|secret|token|api[_-]?key)=.+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)://[^/@:\s]+:[^/@\s]+@"),
    re.compile(r"(?i)x-amz-(?:credential|signature)=.+"),
)


class RecoveryOrchestratorError(RuntimeError):
    """Raised when a recovery plan is unsafe or cannot complete."""


@dataclass(frozen=True)
class Phase:
    name: str
    command: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class PhaseGroup:
    name: str
    parallel: bool
    phases: tuple[Phase, ...]


@dataclass(frozen=True)
class RecoveryPlan:
    drill_id: str
    preflight_groups: tuple[PhaseGroup, ...]
    recovery_groups: tuple[PhaseGroup, ...]
    cleanup_phases: tuple[Phase, ...]
    latest_recoverable_record: Path
    latest_recoverable_field: str
    application_restored_phase: str
    rpo_objective_seconds: float
    rto_objective_seconds: float


Runner = Callable[[Sequence[str], float], int]
UtcNow = Callable[[], datetime]
Monotonic = Callable[[], float]


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryOrchestratorError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_name(value: object, label: str) -> str:
    name = _non_empty_string(value, label)
    if NAME_PATTERN.fullmatch(name) is None:
        raise RecoveryOrchestratorError(f"{label} has an unsafe value")
    return name


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RecoveryOrchestratorError(f"{label} must be a positive number")
    return float(value)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise RecoveryOrchestratorError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RecoveryOrchestratorError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise RecoveryOrchestratorError(f"{label} must include a timezone")
    return parsed


def _validate_command(command: object, label: str) -> tuple[str, ...]:
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)) or not command:
        raise RecoveryOrchestratorError(f"{label} must be a non-empty argv list")
    result = tuple(_non_empty_string(item, f"{label} item") for item in command)
    for argument in result:
        if any(pattern.search(argument) for pattern in SENSITIVE_ARGUMENT_PATTERNS):
            raise RecoveryOrchestratorError(f"{label} contains a sensitive argument")
    return result


def _parse_phase(value: object, label: str) -> Phase:
    if not isinstance(value, Mapping):
        raise RecoveryOrchestratorError(f"{label} must be an object")
    return Phase(
        name=_safe_name(value.get("name"), f"{label}.name"),
        command=_validate_command(value.get("command"), f"{label}.command"),
        timeout_seconds=_positive_number(
            value.get("timeout_seconds", 300), f"{label}.timeout_seconds"
        ),
    )


def _parse_groups(value: object, label: str) -> tuple[PhaseGroup, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RecoveryOrchestratorError(f"{label} must be a list")
    groups: list[PhaseGroup] = []
    for index, raw_group in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(raw_group, Mapping):
            raise RecoveryOrchestratorError(f"{item_label} must be an object")
        raw_phases = raw_group.get("phases")
        if not isinstance(raw_phases, Sequence) or isinstance(raw_phases, (str, bytes)):
            raise RecoveryOrchestratorError(f"{item_label}.phases must be a non-empty list")
        phases = tuple(
            _parse_phase(phase, f"{item_label}.phases[{phase_index}]")
            for phase_index, phase in enumerate(raw_phases)
        )
        if not phases:
            raise RecoveryOrchestratorError(f"{item_label}.phases must be a non-empty list")
        parallel = raw_group.get("parallel", False)
        if not isinstance(parallel, bool):
            raise RecoveryOrchestratorError(f"{item_label}.parallel must be boolean")
        groups.append(
            PhaseGroup(
                name=_safe_name(raw_group.get("name"), f"{item_label}.name"),
                parallel=parallel,
                phases=phases,
            )
        )
    return tuple(groups)


def load_plan(path: Path) -> RecoveryPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryOrchestratorError("recovery plan could not be read") from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise RecoveryOrchestratorError("recovery plan schema_version must be 1")
    preflight = _parse_groups(payload.get("preflight_groups", []), "preflight_groups")
    recovery = _parse_groups(payload.get("recovery_groups"), "recovery_groups")
    if not recovery:
        raise RecoveryOrchestratorError("recovery_groups must not be empty")
    cleanup_value = payload.get("cleanup_phases", [])
    if not isinstance(cleanup_value, Sequence) or isinstance(cleanup_value, (str, bytes)):
        raise RecoveryOrchestratorError("cleanup_phases must be a list")
    cleanup = tuple(
        _parse_phase(value, f"cleanup_phases[{index}]") for index, value in enumerate(cleanup_value)
    )
    all_phases = [phase for group in (*preflight, *recovery) for phase in group.phases]
    names = [phase.name for phase in all_phases]
    if len(names) != len(set(names)):
        raise RecoveryOrchestratorError("phase names must be unique")
    restored_phase = _safe_name(
        payload.get("application_restored_phase"), "application_restored_phase"
    )
    if restored_phase not in {phase.name for group in recovery for phase in group.phases}:
        raise RecoveryOrchestratorError("application_restored_phase must name a recovery phase")
    record = Path(
        _non_empty_string(payload.get("latest_recoverable_record"), "latest_recoverable_record")
    )
    return RecoveryPlan(
        drill_id=_safe_name(payload.get("drill_id"), "drill_id"),
        preflight_groups=preflight,
        recovery_groups=recovery,
        cleanup_phases=cleanup,
        latest_recoverable_record=record,
        latest_recoverable_field=_safe_name(
            payload.get("latest_recoverable_field", "started_at"),
            "latest_recoverable_field",
        ),
        application_restored_phase=restored_phase,
        rpo_objective_seconds=_positive_number(
            payload.get("rpo_objective_seconds"), "rpo_objective_seconds"
        ),
        rto_objective_seconds=_positive_number(
            payload.get("rto_objective_seconds"), "rto_objective_seconds"
        ),
    )


def _default_runner(command: Sequence[str], timeout_seconds: float) -> int:
    result = subprocess.run(
        list(command),
        check=False,
        env=os.environ.copy(),
        timeout=timeout_seconds,
    )
    return result.returncode


def _run_phase(
    phase: Phase,
    *,
    runner: Runner,
    utc_now: UtcNow,
    monotonic: Monotonic,
) -> dict[str, Any]:
    started_at = utc_now()
    started = monotonic()
    error_type: str | None = None
    try:
        return_code = runner(phase.command, phase.timeout_seconds)
    except Exception as error:
        return_code = None
        error_type = type(error).__name__
    completed_at = utc_now()
    duration_seconds = max(0.0, monotonic() - started)
    return {
        "name": phase.name,
        "command": Path(phase.command[0]).name,
        "status": "passed" if return_code == 0 and error_type is None else "failed",
        "return_code": return_code,
        "error_type": error_type,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": duration_seconds,
    }


def _run_group(
    group: PhaseGroup,
    *,
    runner: Runner,
    utc_now: UtcNow,
    monotonic: Monotonic,
) -> list[dict[str, Any]]:
    if not group.parallel or len(group.phases) == 1:
        return [
            _run_phase(phase, runner=runner, utc_now=utc_now, monotonic=monotonic)
            for phase in group.phases
        ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(group.phases)) as executor:
        futures = [
            executor.submit(
                _run_phase,
                phase,
                runner=runner,
                utc_now=utc_now,
                monotonic=monotonic,
            )
            for phase in group.phases
        ]
        return [future.result() for future in futures]


def _latest_recoverable(plan: RecoveryPlan) -> datetime:
    try:
        payload = json.loads(plan.latest_recoverable_record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryOrchestratorError(
            "latest recoverable data record could not be read"
        ) from error
    if not isinstance(payload, Mapping):
        raise RecoveryOrchestratorError("latest recoverable data record must be an object")
    return _timestamp(
        payload.get(plan.latest_recoverable_field),
        f"latest recoverable data record {plan.latest_recoverable_field}",
    )


def run_plan(
    plan: RecoveryPlan,
    *,
    runner: Runner = _default_runner,
    utc_now: UtcNow = lambda: datetime.now(UTC),
    monotonic: Monotonic = time.monotonic,
) -> dict[str, Any]:
    started_at = utc_now()
    phase_results: list[dict[str, Any]] = []
    cleanup_results: list[dict[str, Any]] = []
    failure_declared_at: datetime | None = None
    latest_recoverable_data_at: datetime | None = None
    application_restored_at: datetime | None = None
    primary_failure: str | None = None
    try:
        for group in plan.preflight_groups:
            results = _run_group(group, runner=runner, utc_now=utc_now, monotonic=monotonic)
            phase_results.extend(results)
            if any(result["status"] != "passed" for result in results):
                primary_failure = f"preflight group failed: {group.name}"
                break
        if primary_failure is None:
            latest_recoverable_data_at = _latest_recoverable(plan)
            failure_declared_at = utc_now()
            if latest_recoverable_data_at > failure_declared_at:
                raise RecoveryOrchestratorError(
                    "latest recoverable data follows failure declaration"
                )
            for group in plan.recovery_groups:
                results = _run_group(group, runner=runner, utc_now=utc_now, monotonic=monotonic)
                phase_results.extend(results)
                for result in results:
                    if (
                        result["name"] == plan.application_restored_phase
                        and result["status"] == "passed"
                    ):
                        application_restored_at = _timestamp(
                            result["completed_at"], "application restored phase completion"
                        )
                if any(result["status"] != "passed" for result in results):
                    primary_failure = f"recovery group failed: {group.name}"
                    break
    except Exception as error:
        primary_failure = type(error).__name__
    finally:
        for phase in plan.cleanup_phases:
            cleanup_results.append(
                _run_phase(phase, runner=runner, utc_now=utc_now, monotonic=monotonic)
            )

    completed_at = utc_now()
    rpo_seconds = (
        (failure_declared_at - latest_recoverable_data_at).total_seconds()
        if failure_declared_at is not None and latest_recoverable_data_at is not None
        else None
    )
    rto_seconds = (
        (application_restored_at - failure_declared_at).total_seconds()
        if application_restored_at is not None and failure_declared_at is not None
        else None
    )
    rpo_passed = rpo_seconds is not None and rpo_seconds <= plan.rpo_objective_seconds
    rto_passed = rto_seconds is not None and rto_seconds <= plan.rto_objective_seconds
    cleanup_passed = all(result["status"] == "passed" for result in cleanup_results)
    status = (
        "passed"
        if primary_failure is None and rpo_passed and rto_passed and cleanup_passed
        else "failed"
    )
    return {
        "schema_version": 1,
        "operation": "recovery-drill-orchestrator",
        "drill_id": plan.drill_id,
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "failure_declared_at": (
            failure_declared_at.isoformat() if failure_declared_at is not None else None
        ),
        "latest_recoverable_data_at": (
            latest_recoverable_data_at.isoformat()
            if latest_recoverable_data_at is not None
            else None
        ),
        "application_restored_at": (
            application_restored_at.isoformat() if application_restored_at is not None else None
        ),
        "measurements": {
            "rpo_seconds": rpo_seconds,
            "rto_seconds": rto_seconds,
            "rpo_objective_seconds": plan.rpo_objective_seconds,
            "rto_objective_seconds": plan.rto_objective_seconds,
            "rpo_status": "passed" if rpo_passed else "failed",
            "rto_status": "passed" if rto_passed else "failed",
        },
        "phase_results": phase_results,
        "cleanup_results": cleanup_results,
        "primary_failure": primary_failure,
        "secret_handling": {
            "shell_execution": False,
            "command_arguments_persisted": False,
            "inherited_environment_values_persisted": False,
        },
    }


def _write_private_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a measured recovery plan without shell command interpolation"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--confirm", choices=[CONFIRMATION])
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        if args.confirm is None:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "drill_id": plan.drill_id,
                        "preflight_group_count": len(plan.preflight_groups),
                        "recovery_group_count": len(plan.recovery_groups),
                        "cleanup_phase_count": len(plan.cleanup_phases),
                        "rpo_objective_seconds": plan.rpo_objective_seconds,
                        "rto_objective_seconds": plan.rto_objective_seconds,
                    },
                    sort_keys=True,
                )
            )
            return 0
        report = run_plan(plan)
        _write_private_report(args.report_path, report)
    except RecoveryOrchestratorError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "drill_id": report["drill_id"],
                "status": report["status"],
                "rpo_seconds": report["measurements"]["rpo_seconds"],
                "rto_seconds": report["measurements"]["rto_seconds"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
