from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from infra.identity.local_lab import LabFailure, run_validation

pytestmark = pytest.mark.integration


def test_real_keycloak_verification_reset_and_oidc(tmp_path: Path) -> None:
    report = run_validation(tmp_path)
    assert report["status"] == "passed"
    assert report["keycloak_real"] is True
    assert report["mail_boundary"] == "local_http_capture"
    assert report["cloudflare_delivery_verified"] is False
    assert report["resources_removed"] is True


def test_cleanup_failure_cannot_leave_a_passed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_process = subprocess.run
    cleanup_rejected = False

    def reject_after_removal(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal cleanup_rejected
        result = run_process(args, **kwargs)
        if args[:2] == ["docker", "compose"] and "down" in args and result.returncode == 0:
            # The real resources are removed before simulating a process-boundary failure.
            cleanup_rejected = True
            return subprocess.CompletedProcess(args, 1, "", "private-process-output")
        return result

    monkeypatch.setattr(subprocess, "run", reject_after_removal)
    with pytest.raises(LabFailure):
        run_validation(tmp_path)

    assert cleanup_rejected
    saved = (tmp_path / "report.json").read_text(encoding="utf-8")
    report = json.loads(saved)
    assert report["status"] == "failed"
    assert report["failed_stage"] == "cleanup"
    assert report["resources_removed"] is False
    assert report["callback_listener_closed"] is True
    assert "private-process-output" not in saved
