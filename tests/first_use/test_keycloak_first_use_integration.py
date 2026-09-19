from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from infra.identity.local_lab import LabFailure

from tests.first_use.keycloak_fixture import KeycloakFixture

pytestmark = pytest.mark.integration


def test_real_product_identity_starts_unverified_and_removes_resources(tmp_path: Path) -> None:
    fixture = KeycloakFixture(tmp_path)
    try:
        fixture.start()
        state = fixture.snapshot()
        assert state["version"] == "26.7.0"
        assert state["verifiedUsers"] == 0
        assert state["successfulCodeExchanges"] == 0
        assert state["capturedEmails"] == 0
        assert all(
            state[name]
            for name in (
                "pkceRequired",
                "confidentialClient",
                "implicitDisabled",
                "passwordGrantDisabled",
                "exactCallback",
            )
        )
        assert fixture.settings.issuer.startswith("http://localhost:")
        assert fixture.settings.web_origin == "http://127.0.0.1:5173"
    finally:
        fixture.close()
    assert json.loads((tmp_path / "keycloak-cleanup.json").read_bytes())["status"] == "passed"


def test_real_product_identity_cleanup_error_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = KeycloakFixture(tmp_path)
    run = subprocess.run
    rejected = False

    def reject_after_removal(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal rejected
        result = run(args, **kwargs)
        if args[:2] == ["docker", "compose"] and "down" in args and result.returncode == 0:
            rejected = True
            return subprocess.CompletedProcess(args, 1, "", "private-process-output")
        return result

    try:
        fixture.start()
    except BaseException:
        fixture.close()
        raise
    monkeypatch.setattr(subprocess, "run", reject_after_removal)
    with pytest.raises(LabFailure):
        fixture.close()
    assert rejected
    content = (tmp_path / "keycloak-cleanup.json").read_text(encoding="utf-8")
    report = json.loads(content)
    assert report["status"] == "failed"
    assert report["resourcesRemoved"] is True
    assert "private-process-output" not in content
