from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.run_recovery_orchestrator import (
    RecoveryOrchestratorError,
    load_plan,
    main,
    run_plan,
)


def _write_plan(
    tmp_path: Path,
    *,
    recovery_groups: list[dict[str, object]] | None = None,
    cleanup_phases: list[dict[str, object]] | None = None,
) -> Path:
    record = tmp_path / "latest.json"
    record.write_text(
        json.dumps({"started_at": "2026-08-06T00:00:10+00:00"}),
        encoding="utf-8",
    )
    plan = {
        "schema_version": 1,
        "drill_id": "m6-rto-repeat",
        "preflight_groups": [
            {
                "name": "backup",
                "parallel": False,
                "phases": [
                    {
                        "name": "capture-backup",
                        "command": ["uv", "run", "backup"],
                        "timeout_seconds": 30,
                    }
                ],
            }
        ],
        "recovery_groups": recovery_groups
        if recovery_groups is not None
        else [
            {
                "name": "restore",
                "parallel": False,
                "phases": [
                    {
                        "name": "restore-data",
                        "command": ["uv", "run", "restore"],
                        "timeout_seconds": 60,
                    }
                ],
            },
            {
                "name": "smoke",
                "parallel": False,
                "phases": [
                    {
                        "name": "application-smoke",
                        "command": ["uv", "run", "smoke"],
                        "timeout_seconds": 60,
                    }
                ],
            },
        ],
        "cleanup_phases": cleanup_phases
        if cleanup_phases is not None
        else [
            {
                "name": "stop-services",
                "command": ["docker", "compose", "stop"],
                "timeout_seconds": 30,
            }
        ],
        "latest_recoverable_record": record.as_posix(),
        "latest_recoverable_field": "started_at",
        "application_restored_phase": "application-smoke",
        "rpo_objective_seconds": 300,
        "rto_objective_seconds": 1800,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


class _SequenceClock:
    def __init__(self, values: list[object]) -> None:
        self._values = iter(values)

    def __call__(self):  # type: ignore[no-untyped-def]
        return next(self._values)


def test_main_defaults_to_dry_run_without_executing_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = _write_plan(tmp_path)
    report_path = tmp_path / "report.json"

    assert main(["--plan", str(plan_path), "--report-path", str(report_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "cleanup_phase_count": 1,
        "drill_id": "m6-rto-repeat",
        "dry_run": True,
        "preflight_group_count": 1,
        "recovery_group_count": 2,
        "rpo_objective_seconds": 300.0,
        "rto_objective_seconds": 1800.0,
    }
    assert not report_path.exists()


def test_plan_rejects_credentials_in_command_arguments(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["recovery_groups"][0]["phases"][0]["command"] = [
        "uv",
        "run",
        "restore",
        "postgresql://operator:super-secret@example.invalid/database",
    ]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(RecoveryOrchestratorError, match="sensitive argument"):
        load_plan(plan_path)


def test_run_plan_calculates_rpo_rto_and_persists_no_arguments(tmp_path: Path) -> None:
    plan = load_plan(_write_plan(tmp_path))
    base = datetime(2026, 8, 6, tzinfo=UTC)
    utc_now = _SequenceClock(
        [
            base,
            base + timedelta(seconds=1),
            base + timedelta(seconds=2),
            base + timedelta(seconds=20),
            base + timedelta(seconds=21),
            base + timedelta(seconds=25),
            base + timedelta(seconds=26),
            base + timedelta(seconds=40),
            base + timedelta(seconds=41),
            base + timedelta(seconds=42),
            base + timedelta(seconds=43),
        ]
    )
    monotonic = _SequenceClock([0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0])
    commands: list[list[str]] = []

    def runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> int:
        commands.append(list(command))
        assert timeout_seconds > 0
        return 0

    result = run_plan(plan, runner=runner, utc_now=utc_now, monotonic=monotonic)

    assert result["status"] == "passed"
    assert result["measurements"] == {
        "rpo_seconds": 10.0,
        "rto_seconds": 20.0,
        "rpo_objective_seconds": 300.0,
        "rto_objective_seconds": 1800.0,
        "rpo_status": "passed",
        "rto_status": "passed",
    }
    assert [item["command"] for item in result["phase_results"]] == ["uv", "uv", "uv"]
    assert result["cleanup_results"][0]["command"] == "docker"
    rendered = json.dumps(result)
    assert "run backup" not in rendered
    assert result["secret_handling"]["shell_execution"] is False
    assert len(commands) == 4


def test_failed_group_stops_later_groups_and_cleanup_still_runs(tmp_path: Path) -> None:
    plan = load_plan(_write_plan(tmp_path))
    observed: list[str] = []

    def runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> int:
        del timeout_seconds
        observed.append(command[-1])
        return 1 if command[-1] == "restore" else 0

    result = run_plan(plan, runner=runner)

    assert result["status"] == "failed"
    assert result["primary_failure"] == "recovery group failed: restore"
    assert observed == ["backup", "restore", "stop"]
    assert result["application_restored_at"] is None
    assert result["measurements"]["rto_seconds"] is None
    assert result["cleanup_results"][0]["status"] == "passed"


def test_parallel_group_executes_with_bounded_overlap(tmp_path: Path) -> None:
    parallel_group = [
        {
            "name": "parallel-restore",
            "parallel": True,
            "phases": [
                {
                    "name": "restore-data",
                    "command": ["uv", "run", "restore-data"],
                    "timeout_seconds": 60,
                },
                {
                    "name": "application-smoke",
                    "command": ["uv", "run", "application-smoke"],
                    "timeout_seconds": 60,
                },
            ],
        }
    ]
    plan = load_plan(_write_plan(tmp_path, recovery_groups=parallel_group, cleanup_phases=[]))
    plan.latest_recoverable_record.write_text(
        json.dumps({"started_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}),
        encoding="utf-8",
    )
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> int:
        nonlocal active, maximum_active
        del timeout_seconds
        if command[-1] == "backup":
            return 0
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return 0

    result = run_plan(plan, runner=runner)

    assert result["status"] == "passed"
    assert maximum_active == 2
    assert [item["name"] for item in result["phase_results"][-2:]] == [
        "restore-data",
        "application-smoke",
    ]
