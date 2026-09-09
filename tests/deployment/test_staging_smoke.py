from __future__ import annotations

import hashlib
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
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
    def __init__(self, *, run_status: str = "succeeded") -> None:
        self.calls: list[tuple[str, str]] = []
        self.run_status = run_status
        self.artifact_body = json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-1",
                "answer_text": "The period is thirty days.",
                "citations": [
                    {
                        "document_version_id": "version-1",
                        "excerpt": "The evidence retention period is thirty days.",
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

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
            return {"status": self.run_status}
        if path == "/api/agent-runs/run-1/artifacts":
            return [
                {
                    "artifactId": "artifact-1",
                    "kind": "answer",
                    "status": "draft_ready",
                    "contentSha256": hashlib.sha256(self.artifact_body).hexdigest(),
                    "sizeBytes": len(self.artifact_body),
                }
            ]
        if path == "/api/agent-artifacts/artifact-1/download":
            return {
                "url": "https://objects.example/download",
                "contentSha256": hashlib.sha256(self.artifact_body).hexdigest(),
                "sizeBytes": len(self.artifact_body),
            }
        raise AssertionError(path)

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str:
        assert url == "https://objects.example/signed"
        assert content
        assert headers == {"x-checksum": "ok"}
        self.calls.append(("PUT", url))
        return '"etag-1"'

    def get_bytes(self, url: str) -> bytes:
        assert url == "https://objects.example/download"
        self.calls.append(("GET", url))
        return self.artifact_body


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
        ("GET", "/api/agent-runs/run-1/artifacts"),
        ("GET", "/api/agent-artifacts/artifact-1/download"),
        ("GET", "https://objects.example/download"),
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
    timeouts: list[float] = []

    def fake_urlopen(request: Request, *, timeout: float) -> addinfourl:
        captured.append(request)
        timeouts.append(timeout)
        response = addinfourl(BytesIO(b"{}"), {}, "https://staging.example/api", 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(staging_smoke, "_open_url_no_redirect", fake_urlopen)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="https://staging.example",
        token="redacted",
        allowed_control_plane_hosts=("staging.example",),
    )
    assert client.request_json("POST", "/api/test", payload={}) == {}
    assert len(captured) == 1
    request = captured[0]
    assert request.headers["User-agent"] == "enterprise-doc-staging-smoke/1.0"
    assert timeouts == [90.0]


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


def test_loopback_http_requires_explicit_mode_and_exact_allowlist() -> None:
    staging_smoke.UrlLibSmokeClient(
        base_url="http://127.0.0.1:18000",
        token="redacted",
        allowed_control_plane_hosts=("127.0.0.1",),
        allow_loopback_http=True,
    )
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        staging_smoke.UrlLibSmokeClient(
            base_url="http://staging.example",
            token="redacted",
            allowed_control_plane_hosts=("staging.example",),
            allow_loopback_http=True,
        )


def test_loopback_http_mode_applies_to_allowlisted_object_store_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> addinfourl:
        del timeout
        captured.append(request)
        headers = {"ETag": '"etag-1"'} if request.method == "PUT" else {}
        body = b"" if request.method == "PUT" else b"artifact"
        response = addinfourl(BytesIO(body), headers, request.full_url, 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(staging_smoke, "_open_url_no_redirect", fake_urlopen)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="http://127.0.0.1:18000",
        token="redacted",
        allowed_control_plane_hosts=("127.0.0.1",),
        allowed_object_store_hosts=("127.0.0.1",),
        allow_loopback_http=True,
    )

    assert (
        client.put_bytes(
            "http://127.0.0.1:19000/documents/upload",
            content=b"document",
            headers={},
        )
        == '"etag-1"'
    )
    assert client.get_bytes("http://127.0.0.1:19000/artifacts/download") == b"artifact"
    assert [request.full_url for request in captured] == [
        "http://127.0.0.1:19000/documents/upload",
        "http://127.0.0.1:19000/artifacts/download",
    ]
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        client.get_bytes("http://staging.example/artifacts/download")


def test_loopback_control_plane_allows_allowlisted_https_object_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> addinfourl:
        del timeout
        captured.append(request)
        headers = {"ETag": '"etag-1"'} if request.method == "PUT" else {}
        response = addinfourl(BytesIO(b"artifact"), headers, request.full_url, 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(staging_smoke, "_open_url_no_redirect", fake_urlopen)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="http://127.0.0.1:18000",
        token="redacted",
        allowed_control_plane_hosts=("127.0.0.1",),
        allowed_object_store_hosts=("objects.example",),
        allow_loopback_http=True,
    )

    assert (
        client.put_bytes(
            "https://objects.example/documents/upload",
            content=b"document",
            headers={},
        )
        == '"etag-1"'
    )
    assert client.get_bytes("https://objects.example/artifacts/download") == b"artifact"
    assert [request.full_url for request in captured] == [
        "https://objects.example/documents/upload",
        "https://objects.example/artifacts/download",
    ]
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        client.get_bytes("https://other.example/artifacts/download")
    with pytest.raises(staging_smoke.StagingSmokeFailure):
        client.get_bytes("http://objects.example/artifacts/download")


def test_object_store_redirects_are_rejected_before_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RedirectingOpener:
        def open(self, request: Request, *, timeout: float) -> None:
            del timeout
            raise HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "http://localhost:19001/private"},
                None,
            )

    handlers: list[object] = []

    def fake_build_opener(*items: object) -> RedirectingOpener:
        handlers.extend(items)
        return RedirectingOpener()

    monkeypatch.setattr(staging_smoke, "build_opener", fake_build_opener)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="http://127.0.0.1:18000",
        token="redacted",
        allowed_control_plane_hosts=("127.0.0.1",),
        allowed_object_store_hosts=("127.0.0.1",),
        allow_loopback_http=True,
    )

    with pytest.raises(staging_smoke.StagingSmokeFailure, match="HTTP 302"):
        client.get_bytes("http://127.0.0.1:19000/artifacts/download")
    assert len(handlers) == 1
    assert isinstance(handlers[0], staging_smoke._RejectRedirectHandler)


def test_staging_smoke_rejects_tampered_artifact_download() -> None:
    client = FakeClient()
    client.artifact_body = b"tampered"

    with pytest.raises(staging_smoke.StagingSmokeFailure, match="valid JSON"):
        staging_smoke.run_staging_smoke(
            client,
            timeout_seconds=30,
            monotonic=lambda: 1.0,
            sleep=lambda _: None,
        )


def _configure_cli(
    monkeypatch: pytest.MonkeyPatch,
    report_path: Path,
    client: FakeClient,
) -> None:
    monkeypatch.setenv("STAGING_SMOKE_TOKEN", "test-only-bearer-secret")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--base-url",
            "https://staging.example",
            "--allowed-host",
            "staging.example",
            "--allowed-object-store-host",
            "objects.example",
            "--report-path",
            str(report_path),
        ],
    )
    monkeypatch.setattr(staging_smoke, "UrlLibSmokeClient", lambda **_: client)


@pytest.mark.parametrize(
    "terminal_status", ["cancelled", "expired", "failed", "refused", "rejected"]
)
def test_main_writes_failed_agent_report_before_exiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    terminal_status: str,
) -> None:
    class FailedAgentClient(FakeClient):
        def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
            result = super().request_json(method, path, **kwargs)
            if path == "/api/agent-runs/run-1":
                return {
                    "status": terminal_status,
                    "error": "https://objects.example/private?signature=unsafe-secret",
                    "diagnosticCode": None,
                    "inputText": "private prompt",
                }
            return result

    client = FailedAgentClient()
    report_path = tmp_path / "nested" / "smoke.json"
    _configure_cli(monkeypatch, report_path, client)

    with pytest.raises(SystemExit) as stopped:
        staging_smoke.main()

    assert stopped.value.code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report
    assert captured.err == ""
    assert report["schema_version"] == 2
    assert report["status"] == "failed"
    assert report["steps"] == [
        "upload_session_created",
        "object_uploaded",
        "upload_completed",
        "document_ready",
        "agent_run_created",
    ]
    assert report["failure"] == {
        "step": "agent_run_succeeded",
        "code": "agent_terminal_status",
        "http_status": None,
    }
    assert report["sample_count"] == 1
    assert report["agent_terminal_status"] == terminal_status
    assert report["correlation_sha256"] == {
        "upload_session_id": hashlib.sha256(b"session-1").hexdigest(),
        "document_version_id": hashlib.sha256(b"version-1").hexdigest(),
        "agent_run_id": hashlib.sha256(b"run-1").hexdigest(),
    }
    assert client.calls[-1] == ("GET", "/api/agent-runs/run-1")
    assert len(client.calls) == 7
    for private_value in (
        "session-1",
        "version-1",
        "run-1",
        "unsafe-secret",
        "private prompt",
        "test-only-bearer-secret",
    ):
        assert private_value not in captured.out


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "http_status"),
    [
        ("http", "http_error", 401),
        ("network", "transport_error", None),
        ("timeout", "timeout", None),
        ("io", "transport_error", None),
        ("unexpected", "unexpected_error", None),
    ],
)
def test_smoke_failure_reports_only_bounded_transport_metadata(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_code: str,
    http_status: int | None,
) -> None:
    private_message = "Bearer secret-token https://private.example/?signature=secret-signature"
    failures = {
        "http": HTTPError(
            "https://private.example/?signature=secret-signature",
            401,
            private_message,
            {},
            BytesIO(b"private response body"),
        ),
        "network": URLError(private_message),
        "timeout": TimeoutError(private_message),
        "io": OSError(private_message),
        "unexpected": RuntimeError(private_message),
    }
    requests: list[Request] = []

    def failed_urlopen(request: Request, *, timeout: float) -> None:
        del timeout
        requests.append(request)
        raise failures[failure_kind]

    monkeypatch.setattr(staging_smoke, "_open_url_no_redirect", failed_urlopen)
    client = staging_smoke.UrlLibSmokeClient(
        base_url="https://staging.example",
        token="secret-token",
        allowed_control_plane_hosts=("staging.example",),
    )

    with pytest.raises(staging_smoke.StagingSmokeFailure) as stopped:
        staging_smoke.run_staging_smoke(client, timeout_seconds=30)

    report = stopped.value.report
    assert report is not None
    assert report["status"] == "failed"
    assert report["steps"] == []
    assert report["sample_count"] == 0
    assert report["correlation_sha256"] == {}
    assert report["agent_terminal_status"] is None
    assert report["failure"] == {
        "step": "upload_session_created",
        "code": expected_code,
        "http_status": http_status,
    }
    assert len(requests) == 1
    rendered = json.dumps(report)
    for private_value in ("secret-token", "secret-signature", "private response body"):
        assert private_value not in rendered


@pytest.mark.parametrize("waiting_for", ["document", "agent"])
def test_timeout_report_keeps_only_confirmed_progress(waiting_for: str) -> None:
    class WaitingClient(FakeClient):
        def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
            result = super().request_json(method, path, **kwargs)
            if waiting_for == "document" and path == "/api/agent-runs/ready-document-versions":
                return []
            if waiting_for == "agent" and path == "/api/agent-runs/run-1":
                return {"status": "running", "diagnosticCode": None}
            return result

    elapsed = 0.0

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    client = WaitingClient()
    with pytest.raises(staging_smoke.StagingSmokeFailure) as stopped:
        staging_smoke.run_staging_smoke(
            client, timeout_seconds=1, monotonic=lambda: elapsed, sleep=sleep
        )

    report = stopped.value.report
    assert report is not None
    assert report["status"] == "failed"
    assert report["failure"] == {
        "step": "document_ready" if waiting_for == "document" else "agent_run_succeeded",
        "code": "timeout",
        "http_status": None,
    }
    assert report["agent_terminal_status"] is None
    assert report["sample_count"] == (0 if waiting_for == "document" else 1)
    assert ("document_ready" in report["steps"]) == (waiting_for == "agent")
    assert not any("artifacts" in path for _, path in client.calls)
    assert client.calls.count(("POST", "/api/agent-runs")) == report["sample_count"]


@pytest.mark.parametrize("invalid", ["missing_token", "zero", "nan", "inf", "url"])
def test_main_configuration_failure_writes_empty_progress_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid: str,
) -> None:
    client = FakeClient()
    client_factory = staging_smoke.UrlLibSmokeClient
    report_path = tmp_path / "smoke.json"
    _configure_cli(monkeypatch, report_path, client)
    if invalid == "missing_token":
        monkeypatch.delenv("STAGING_SMOKE_TOKEN")
    elif invalid == "url":
        monkeypatch.setattr(staging_smoke, "UrlLibSmokeClient", client_factory)
        monkeypatch.setattr(sys, "argv", [*sys.argv[:2], "http://staging.example", *sys.argv[3:]])
    else:
        timeout = "0" if invalid == "zero" else invalid
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--timeout-seconds", timeout])

    with pytest.raises(SystemExit) as stopped:
        staging_smoke.main()

    assert stopped.value.code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == report
    assert report["status"] == "failed"
    assert report["steps"] == []
    assert report["correlation_sha256"] == {}
    assert report["sample_count"] == 0
    assert report["agent_terminal_status"] is None
    assert report["failure"] == {
        "step": "configuration",
        "code": "invalid_configuration",
        "http_status": None,
    }
    assert client.calls == []


@pytest.mark.parametrize("run_status", ["succeeded", "failed"])
def test_main_report_write_failure_keeps_stdout_report_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_status: str,
) -> None:
    # An existing directory cannot be used as the report file on Windows or POSIX.
    _configure_cli(monkeypatch, tmp_path, FakeClient(run_status=run_status))

    with pytest.raises(SystemExit) as stopped:
        staging_smoke.main()

    assert stopped.value.code == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "failed"
    assert report["agent_terminal_status"] == run_status
    assert report["report_output"] == {"status": "failed", "code": "report_write_failed"}
    assert report["failure"]["code"] == (
        "report_write_failed" if run_status == "succeeded" else "agent_terminal_status"
    )
    assert ("answer_citation_verified" in report["steps"]) == (run_status == "succeeded")
    assert captured.err == "Staging smoke report could not be written.\n"
    assert str(tmp_path) not in captured.out + captured.err


@pytest.mark.parametrize("malformed_at", ["upload", "ready", "run"])
def test_malformed_response_does_not_invent_progress_or_copy_response_text(
    malformed_at: str,
) -> None:
    class MalformedClient(FakeClient):
        def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
            result = super().request_json(method, path, **kwargs)
            if malformed_at == "upload" and path == "/api/upload-sessions":
                return {"error": "private-response-text"}
            if malformed_at == "ready" and path == "/api/agent-runs/ready-document-versions":
                return ["private-response-text"]
            if malformed_at == "run" and path == "/api/agent-runs/run-1":
                return {"status": {"body": "private-response-text"}}
            return result

    with pytest.raises(staging_smoke.StagingSmokeFailure) as stopped:
        staging_smoke.run_staging_smoke(MalformedClient(), timeout_seconds=1)

    report = stopped.value.report
    assert report["failure"] == {
        "step": {
            "upload": "upload_session_created",
            "ready": "document_ready",
            "run": "agent_run_succeeded",
        }[malformed_at],
        "code": "contract_validation_failed",
        "http_status": None,
    }
    assert report["sample_count"] == (1 if malformed_at == "run" else 0)
    assert report["agent_terminal_status"] is None
    assert "agent_run_succeeded" not in report["steps"]
    assert "private-response-text" not in json.dumps(report)


@pytest.mark.parametrize("artifact_failure", ["sha256", "json", "encoding", "citation"])
def test_failed_artifact_report_preserves_completed_validation_steps(artifact_failure: str) -> None:
    class ArtifactClient(FakeClient):
        def get_bytes(self, url: str) -> bytes:
            body = super().get_bytes(url)
            return b"X" * len(body) if artifact_failure == "sha256" else body

    client = ArtifactClient()
    if artifact_failure == "json":
        client.artifact_body = b"private invalid json"
    elif artifact_failure == "encoding":
        client.artifact_body = b"\xffprivate invalid encoding"
    elif artifact_failure == "citation":
        payload = json.loads(client.artifact_body)
        payload["citations"][0]["excerpt"] = "private unsupported citation"
        client.artifact_body = json.dumps(payload).encode()

    with pytest.raises(staging_smoke.StagingSmokeFailure) as stopped:
        staging_smoke.run_staging_smoke(client, timeout_seconds=30)

    report = stopped.value.report
    assert report["status"] == "failed"
    assert report["agent_terminal_status"] == "succeeded"
    assert "answer_artifact_downloaded" in report["steps"]
    assert ("answer_artifact_sha256_verified" in report["steps"]) == (artifact_failure != "sha256")
    assert "answer_citation_verified" not in report["steps"]
    assert report["failure"]["code"] == "contract_validation_failed"
    assert report["failure"]["step"] == (
        "answer_artifact_sha256_verified"
        if artifact_failure == "sha256"
        else "answer_citation_verified"
    )
    assert report["correlation_sha256"]["artifact_id"] == hashlib.sha256(b"artifact-1").hexdigest()
    assert "private" not in json.dumps(report)


def test_main_success_writes_the_same_passed_report_to_file_and_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "smoke.json"
    client = FakeClient()
    _configure_cli(monkeypatch, report_path, client)

    staging_smoke.main()

    captured = capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert json.loads(captured.out) == report
    assert captured.err == ""
    assert report["status"] == "passed"
    assert report["sample_count"] == 1
    assert report["agent_terminal_status"] == "succeeded"
    assert "failure" not in report
    assert report["steps"][-4:] == [
        "answer_artifact_listed",
        "answer_artifact_downloaded",
        "answer_artifact_sha256_verified",
        "answer_citation_verified",
    ]
    assert len(client.calls) == 10
