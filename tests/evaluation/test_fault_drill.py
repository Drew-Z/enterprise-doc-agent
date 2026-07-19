from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from enterprise_doc_core.evaluation import verify_report_payload

SCRIPT = Path(__file__).parents[2] / "scripts" / "fault_drill.py"
SPEC = spec_from_file_location("fault_drill_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fault_drill = module_from_spec(SPEC)
sys.modules[SPEC.name] = fault_drill
SPEC.loader.exec_module(fault_drill)


def test_fault_drill_plans_preserve_lease_and_recovery_constraints() -> None:
    redis_plan = " ".join(fault_drill.build_plan("redis")).lower()
    worker_plan = " ".join(fault_drill.build_plan("worker-lease")).lower()
    assert "publishing lease" in redis_plan
    assert "hard-kill" in worker_plan
    assert "different worker id" in worker_plan
    assert "fenced" in worker_plan


def test_fault_drill_rejects_non_local_environments() -> None:
    fault_drill.validate_environment("local")
    fault_drill.validate_environment("test")
    with pytest.raises(fault_drill.FaultDrillFailure):
        fault_drill.validate_environment("staging")
    with pytest.raises(fault_drill.FaultDrillFailure):
        fault_drill.validate_environment("production")


def test_fault_drill_source_requires_confirmation_and_has_cleanup() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'CONFIRMATION = "local-fault-drill"' in source
    assert "if service_needs_recovery" in source
    assert 'cleanup["error"]' in source


def test_fault_drill_reports_cleanup_failure_without_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_calls = 0
    command_calls = 0

    def wait_for_readiness(*args: object, **kwargs: object) -> None:
        nonlocal readiness_calls
        readiness_calls += 1
        if readiness_calls == 2:
            raise fault_drill.FaultDrillFailure("outage was not observed")

    def run_command(command: list[str]) -> None:
        nonlocal command_calls
        command_calls += 1
        if command_calls == 2:
            raise fault_drill.FaultDrillFailure("cleanup restart failed")

    monkeypatch.setattr(fault_drill, "_wait_for_readiness", wait_for_readiness)
    monkeypatch.setattr(fault_drill, "_run", run_command)

    report = fault_drill.run_dependency_drill(
        scenario="redis",
        environment="test",
        readiness_url="http://readiness.test",
        timeout_seconds=1,
    )

    assert report["status"] == "failed"
    assert report["failure"] == "outage was not observed"
    assert report["cleanup"]["attempted"] is True
    assert report["cleanup"]["error"] == "cleanup restart failed"
    assert all("outbox" not in step["name"] for step in report["steps"])
    assert verify_report_payload(report)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "target", "error_code"),
    [
        ("handler-retryable", "handler", "fault_injected_retryable"),
        ("model-timeout", "model", "model_timeout"),
        ("mcp-timeout", "mcp", "mcp_client_timeout"),
        ("object-store-unavailable", "multipart", "object_store_unavailable"),
    ],
)
async def test_deterministic_fault_drills_emit_verifiable_reports(
    scenario: str,
    target: str,
    error_code: str,
) -> None:
    report = await fault_drill.run_deterministic_fault_drill(
        scenario=scenario,
        environment="test",
    )
    payload = report.model_dump(mode="json")

    assert report.status == "passed"
    assert report.injection["target"] == target
    assert report.observed["error_code"] == error_code
    assert report.observed["one_shot_delegation"] is True
    assert report.side_effects["delegated_calls"] == 1
    assert report.provenance.command
    assert len(report.provenance.commit_sha) == 40
    assert report.provenance.payload_sha256
    assert verify_report_payload(payload)

    observed = payload["observed"]
    assert isinstance(observed, dict)
    observed["one_shot_delegation"] = False
    assert not verify_report_payload(payload)


def test_fault_drill_main_writes_sealed_failure_report_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "fault.json"

    async def fail(**_: object) -> object:
        raise RuntimeError("sensitive runtime detail")

    monkeypatch.setattr(fault_drill, "run_deterministic_fault_drill", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fault_drill.py",
            "--scenario",
            "handler-retryable",
            "--run",
            "--environment",
            "test",
            "--confirm",
            fault_drill.CONFIRMATION,
            "--report-path",
            str(report_path),
        ],
    )

    with pytest.raises(SystemExit):
        fault_drill.main()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["observed"]["error_code"] == "fault_drill_unexpected_error"
    assert "sensitive runtime detail" not in report_path.read_text(encoding="utf-8")
    command = " ".join(report["provenance"]["command"])
    assert str(report_path) not in command
    assert "<report-path>" in command
    assert verify_report_payload(report)
