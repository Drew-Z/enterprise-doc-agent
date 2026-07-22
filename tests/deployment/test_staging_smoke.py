from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.response import addinfourl

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "staging_smoke.py"
SPEC = spec_from_file_location("staging_smoke_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
staging_smoke = module_from_spec(SPEC)
sys.modules[SPEC.name] = staging_smoke
SPEC.loader.exec_module(staging_smoke)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        del payload, headers, expected_statuses
        self.calls.append((method, path))
        if path == "/api/upload-sessions":
            return {"sessionId": "session-1"}
        if path.endswith("/parts/1/presign"):
            return {"url": "https://objects.example/signed", "headers": {"x-checksum": "ok"}}
        if path.endswith("/complete"):
            return {"versionId": "version-1"}
        if path == "/api/agent-runs/ready-document-versions":
            return [{"versionId": "version-1"}]
        if path == "/api/agent-runs":
            return {"runId": "run-1"}
        if path == "/api/agent-runs/run-1":
            return {"status": "succeeded"}
        raise AssertionError(path)

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str:
        assert url == "https://objects.example/signed"
        assert content
        assert headers == {"x-checksum": "ok"}
        self.calls.append(("PUT", url))
        return '"etag-1"'


def test_staging_smoke_runs_authenticated_main_path_without_persisting_identifiers() -> None:
    client = FakeClient()
    report = staging_smoke.run_staging_smoke(
        client,
        timeout_seconds=30,
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    )

    assert report["status"] == "passed"
    assert report["scenario"] == "authenticated-upload-ingestion-agent"
    assert "session-1" not in str(report)
    assert "version-1" not in str(report)
    assert "run-1" not in str(report)
    assert client.calls == [
        ("POST", "/api/upload-sessions"),
        ("POST", "/api/upload-sessions/session-1/parts/1/presign"),
        ("PUT", "https://objects.example/signed"),
        ("POST", "/api/upload-sessions/session-1/complete"),
        ("GET", "/api/agent-runs/ready-document-versions"),
        ("POST", "/api/agent-runs"),
        ("GET", "/api/agent-runs/run-1"),
    ]


def test_staging_smoke_source_never_logs_or_accepts_token_as_cli_argument() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'add_argument("--token"' not in source
    assert 'os.environ.get("STAGING_SMOKE_TOKEN"' in source
    assert "print(token" not in source


def test_url_lib_client_sets_explicit_automation_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> addinfourl:
        del timeout
        captured.append(request)
        response = addinfourl(BytesIO(b"{}"), {}, "https://staging.example/api", 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(staging_smoke, "urlopen", fake_urlopen)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="https://staging.example",
        token="redacted",
        allowed_control_plane_hosts=("staging.example",),
    )
    assert client.request_json("POST", "/api/test", payload={}) == {}
    assert len(captured) == 1
    request = captured[0]
    assert request.headers["User-agent"] == "enterprise-doc-staging-smoke/1.0"


def test_staging_smoke_rejects_plaintext_or_unallowlisted_endpoints() -> None:
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        staging_smoke.validate_https_endpoint(
            "http://staging.example",
            allowed_hosts=("staging.example",),
            description="staging base URL",
        )
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        staging_smoke.validate_https_endpoint(
            "https://other.example",
            allowed_hosts=("staging.example",),
            description="staging base URL",
        )
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        staging_smoke.validate_https_endpoint(
            "https://127.0.0.1",
            allowed_hosts=("127.0.0.1",),
            description="presigned object URL",
        )
