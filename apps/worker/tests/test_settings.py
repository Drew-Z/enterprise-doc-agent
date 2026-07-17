from __future__ import annotations

import pytest

from enterprise_doc_worker.config import WorkerSettings


def test_worker_settings_own_probe_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER__HOST", "0.0.0.0")
    monkeypatch.setenv("WORKER__PROBE_PORT", "8181")

    settings = WorkerSettings(_env_file=None)

    assert settings.worker.host == "0.0.0.0"
    assert settings.worker.probe_port == 8181
