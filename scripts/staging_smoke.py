from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

_FAILURE_CODES = frozenset(
    {
        "invalid_configuration",
        "http_error",
        "transport_error",
        "timeout",
        "unexpected_error",
        "agent_terminal_status",
        "contract_validation_failed",
        "report_write_failed",
    }
)


class StagingSmokeFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "contract_validation_failed",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in _FAILURE_CODES else "contract_validation_failed"
        self.http_status = (
            http_status
            if isinstance(http_status, int)
            and not isinstance(http_status, bool)
            and 100 <= http_status <= 599
            else None
        )
        self.report: dict[str, Any] | None = None


@dataclass(slots=True)
class _SmokeProgress:
    started: float
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    step: str = "configuration"
    steps: list[str] = field(default_factory=list)
    correlation_sha256: dict[str, str] = field(default_factory=dict)
    agent_terminal_status: str | None = None

    def record_reference(self, name: str, value: str) -> None:
        self.correlation_sha256[name] = hashlib.sha256(value.encode("utf-8")).hexdigest()

    def complete_step(self, next_step: str) -> None:
        self.steps.append(self.step)
        self.step = next_step

    def report(
        self,
        monotonic: Callable[[], float],
        *,
        failure: StagingSmokeFailure | None = None,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "schema_version": 2,
            "scenario": "authenticated-upload-ingestion-agent",
            "status": "failed" if failure is not None else "passed",
            "steps": list(self.steps),
            "sample_count": int("agent_run_created" in self.steps),
            "correlation_sha256": dict(self.correlation_sha256),
            "agent_terminal_status": self.agent_terminal_status,
            "duration_seconds": max(0.0, monotonic() - self.started),
            "started_at": self.started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "limitations": [
                (
                    "This smoke uses a dedicated synthetic text fixture and must run with a "
                    "dedicated staging tenant."
                ),
                (
                    "It validates one main-path execution, not capacity, failover, model "
                    "quality, or production availability."
                ),
                "The object-store presign endpoint must be reachable from the workflow runner.",
                (
                    "sample_count counts confirmed Agent run creation, not provider requests; "
                    "provider usage, cost and underlying failure diagnostics are not measured."
                ),
            ],
        }
        if failure is not None:
            report["failure"] = {
                "step": self.step,
                "code": failure.code,
                "http_status": failure.http_status,
            }
        return report


_STAGING_SMOKE_USER_AGENT = "enterprise-doc-staging-smoke/1.0"


def _transport_failure_code(error: OSError) -> str:
    if isinstance(error, TimeoutError) or (
        isinstance(error, URLError) and isinstance(error.reason, TimeoutError)
    ):
        return "timeout"
    return "transport_error"


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _open_url_no_redirect(request: Request, *, timeout: float) -> Any:
    return build_opener(_RejectRedirectHandler()).open(request, timeout=timeout)


def validate_https_endpoint(
    value: str,
    *,
    allowed_hosts: tuple[str, ...] = (),
    description: str,
) -> str:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError as error:
        raise StagingSmokeFailure(f"{description} is not a valid URL.") from error
    if parsed.scheme != "https" or not hostname:
        raise StagingSmokeFailure(f"{description} must use HTTPS and include a host.")
    if parsed.username or parsed.password:
        raise StagingSmokeFailure(f"{description} must not contain credentials.")
    normalized_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
    if normalized_hosts and hostname.lower() not in normalized_hosts:
        raise StagingSmokeFailure(f"{description} host is not in the configured allowlist.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        raise StagingSmokeFailure(f"{description} must not target a private or loopback address.")
    return value.rstrip("/")


class SmokeClient(Protocol):
    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]: ...

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str: ...

    def get_bytes(self, url: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class UrlLibSmokeClient:
    base_url: str
    token: str
    timeout_seconds: float = 90.0
    allowed_control_plane_hosts: tuple[str, ...] = ()
    allowed_object_store_hosts: tuple[str, ...] = ()
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        if self.allow_loopback_http:
            self._validate_loopback_control_plane()
        else:
            validate_https_endpoint(
                self.base_url,
                allowed_hosts=self.allowed_control_plane_hosts,
                description="staging base URL",
            )

    def _validate_loopback_control_plane(self) -> None:
        self._validate_loopback_http_endpoint(
            self.base_url,
            allowed_hosts=self.allowed_control_plane_hosts,
            description="staging base URL",
        )

    @staticmethod
    def _validate_loopback_http_endpoint(
        value: str,
        *,
        allowed_hosts: tuple[str, ...],
        description: str,
    ) -> str:
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
        except ValueError as error:
            raise StagingSmokeFailure(f"{description} is not a valid URL.") from error
        normalized_allowed_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
        if parsed.scheme != "http" or not hostname:
            raise StagingSmokeFailure(f"loopback {description} must use HTTP and include a host.")
        if parsed.username or parsed.password:
            raise StagingSmokeFailure(f"{description} must not contain credentials.")
        if not normalized_allowed_hosts or hostname.lower() not in normalized_allowed_hosts:
            raise StagingSmokeFailure(f"{description} host is not in the configured allowlist.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if hostname.lower() != "localhost" and (address is None or not address.is_loopback):
            raise StagingSmokeFailure(
                f"loopback {description} must target localhost or a loopback address."
            )
        return value.rstrip("/")

    def _validate_object_store_url(self, value: str) -> str:
        if self.allow_loopback_http and urlparse(value).scheme == "http":
            return self._validate_loopback_http_endpoint(
                value,
                allowed_hosts=self.allowed_object_store_hosts,
                description="presigned object URL",
            )
        return validate_https_endpoint(
            value,
            allowed_hosts=self.allowed_object_store_hosts,
            description="presigned object URL",
        )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        expected = expected_statuses or {200}
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": _STAGING_SMOKE_USER_AGENT,
                **({"Content-Type": "application/json"} if body is not None else {}),
                **(headers or {}),
            },
        )
        try:
            with _open_url_no_redirect(request, timeout=self.timeout_seconds) as response:
                if response.status not in expected:
                    raise StagingSmokeFailure(
                        f"Control-plane request returned HTTP {response.status}.",
                        code="http_error",
                        http_status=response.status,
                    )
                body = response.read()
                if not body:
                    return {}
                decoded = json.loads(body)
        except HTTPError as error:
            if error.code in expected:
                try:
                    body = error.read()
                    if not body:
                        return {}
                    decoded = json.loads(body)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError) as decode_error:
                    raise StagingSmokeFailure(
                        f"Control-plane request returned HTTP {error.code} without valid JSON.",
                        http_status=error.code,
                    ) from decode_error
                if not isinstance(decoded, (dict, list)):
                    raise StagingSmokeFailure(
                        "Control-plane response was not a JSON object or list."
                    ) from None
                return decoded
            raise StagingSmokeFailure(
                f"Control-plane request returned HTTP {error.code}.",
                code="http_error",
                http_status=error.code,
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise StagingSmokeFailure(
                f"Control-plane request failed with {type(error).__name__}."
            ) from error
        except OSError as error:
            raise StagingSmokeFailure(
                f"Control-plane request failed with {type(error).__name__}.",
                code=_transport_failure_code(error),
            ) from error
        if not isinstance(decoded, (dict, list)):
            raise StagingSmokeFailure("Control-plane response was not a JSON object or list.")
        return decoded

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str:
        validated_url = self._validate_object_store_url(url)
        request = Request(
            validated_url,
            data=content,
            method="PUT",
            headers={
                **headers,
                "Content-Length": str(len(content)),
                "User-Agent": _STAGING_SMOKE_USER_AGENT,
            },
        )
        try:
            with _open_url_no_redirect(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise StagingSmokeFailure(
                        f"Direct object-store upload returned HTTP {response.status}.",
                        code="http_error",
                        http_status=response.status,
                    )
                etag_value = response.headers.get("ETag")
        except HTTPError as error:
            raise StagingSmokeFailure(
                f"Direct object-store upload returned HTTP {error.code}.",
                code="http_error",
                http_status=error.code,
            ) from error
        except OSError as error:
            raise StagingSmokeFailure(
                f"Direct object-store upload failed with {type(error).__name__}.",
                code=_transport_failure_code(error),
            ) from error
        if not isinstance(etag_value, str) or not etag_value:
            raise StagingSmokeFailure("Direct object-store upload omitted its ETag.")
        return etag_value

    def get_bytes(self, url: str) -> bytes:
        validated_url = self._validate_object_store_url(url)
        request = Request(
            validated_url,
            method="GET",
            headers={"User-Agent": _STAGING_SMOKE_USER_AGENT},
        )
        try:
            with _open_url_no_redirect(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise StagingSmokeFailure(
                        f"Direct object-store download returned HTTP {response.status}.",
                        code="http_error",
                        http_status=response.status,
                    )
                content = response.read()
        except HTTPError as error:
            raise StagingSmokeFailure(
                f"Direct object-store download returned HTTP {error.code}.",
                code="http_error",
                http_status=error.code,
            ) from error
        except OSError as error:
            raise StagingSmokeFailure(
                f"Direct object-store download failed with {type(error).__name__}.",
                code=_transport_failure_code(error),
            ) from error
        if not isinstance(content, bytes):
            raise StagingSmokeFailure("Direct object-store download did not return bytes.")
        return content


def _required_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StagingSmokeFailure(f"{description} was not a JSON object.")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise StagingSmokeFailure(f"Response omitted required field {key}.")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StagingSmokeFailure(f"Response omitted required field {key}.")
    return value


def _validate_answer_artifact(
    client: SmokeClient,
    *,
    run_id: str,
    version_id: str,
    progress: _SmokeProgress,
) -> None:
    artifacts = client.request_json("GET", f"/api/agent-runs/{run_id}/artifacts")
    if not isinstance(artifacts, list):
        raise StagingSmokeFailure("Agent artifact response was not a JSON list.")
    answers = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("kind") == "answer"
        and item.get("status") == "draft_ready"
    ]
    if len(answers) != 1:
        raise StagingSmokeFailure("Agent run did not expose exactly one ready answer artifact.")
    answer = answers[0]
    artifact_id = _required_str(answer, "artifactId")
    expected_sha256 = _required_str(answer, "contentSha256")
    expected_size = _required_int(answer, "sizeBytes")
    progress.record_reference("artifact_id", artifact_id)
    progress.complete_step("answer_artifact_downloaded")
    download = _required_mapping(
        client.request_json("GET", f"/api/agent-artifacts/{artifact_id}/download"),
        "Agent artifact download response",
    )
    if _required_str(download, "contentSha256") != expected_sha256:
        raise StagingSmokeFailure("Artifact download metadata changed its SHA-256.")
    if _required_int(download, "sizeBytes") != expected_size:
        raise StagingSmokeFailure("Artifact download metadata changed its byte size.")
    body = client.get_bytes(_required_str(download, "url"))
    progress.complete_step("answer_artifact_sha256_verified")
    if len(body) != expected_size:
        raise StagingSmokeFailure("Downloaded artifact byte size did not match metadata.")
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise StagingSmokeFailure("Downloaded artifact SHA-256 did not match metadata.")
    progress.complete_step("answer_citation_verified")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StagingSmokeFailure("Downloaded artifact was not valid JSON.") from error
    artifact_payload = _required_mapping(payload, "Downloaded artifact")
    if _required_str(artifact_payload, "run_id") != run_id:
        raise StagingSmokeFailure("Downloaded artifact referenced the wrong Agent run.")
    _required_str(artifact_payload, "answer_text")
    citations = artifact_payload.get("citations")
    if not isinstance(citations, list) or not citations:
        raise StagingSmokeFailure("Downloaded artifact did not contain citations.")
    evidence_phrase = "evidence retention period is thirty days"
    if not any(
        isinstance(citation, dict)
        and citation.get("document_version_id") == version_id
        and isinstance(citation.get("excerpt"), str)
        and evidence_phrase in citation["excerpt"].lower()
        for citation in citations
    ):
        raise StagingSmokeFailure(
            "Downloaded artifact did not cite the uploaded evidence retention statement."
        )
    progress.complete_step("completed")


def _wait_for_ready_version(
    client: SmokeClient,
    *,
    version_id: str,
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    while monotonic() < deadline:
        payload = client.request_json("GET", "/api/agent-runs/ready-document-versions")
        if not isinstance(payload, list):
            raise StagingSmokeFailure("Ready-document response was not a JSON list.")
        if any(
            _required_mapping(item, "Ready-document entry").get("versionId") == version_id
            for item in payload
        ):
            return
        sleep(2.0)
    raise StagingSmokeFailure(
        "Document ingestion did not reach ready before the timeout.", code="timeout"
    )


def _wait_for_run(
    client: SmokeClient,
    *,
    run_id: str,
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> str:
    terminal = {"cancelled", "expired", "failed", "refused", "rejected", "succeeded"}
    while monotonic() < deadline:
        payload = _required_mapping(
            client.request_json("GET", f"/api/agent-runs/{run_id}"),
            "Agent status response",
        )
        status = _required_str(payload, "status")
        if status in terminal:
            return status
        sleep(2.0)
    raise StagingSmokeFailure(
        "Agent run did not reach a terminal status before the timeout.", code="timeout"
    )


def run_staging_smoke(
    client: SmokeClient,
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    progress = _SmokeProgress(started=monotonic())
    try:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise StagingSmokeFailure(
                "timeout must be finite and positive", code="invalid_configuration"
            )
        _execute_staging_smoke(
            client,
            progress=progress,
            timeout_seconds=timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
    except StagingSmokeFailure as error:
        error.report = progress.report(monotonic, failure=error)
        raise
    except Exception as error:
        failure = StagingSmokeFailure("Unexpected staging smoke failure.", code="unexpected_error")
        failure.report = progress.report(monotonic, failure=failure)
        raise failure from error
    return progress.report(monotonic)


def _execute_staging_smoke(
    client: SmokeClient,
    *,
    progress: _SmokeProgress,
    timeout_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    progress.step = "upload_session_created"
    content = (
        b"Staging smoke contract. The evidence retention period is thirty days. "
        b"This fixture contains no customer data."
    )
    digest_hex = hashlib.sha256(content).hexdigest()
    checksum_b64 = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    suffix = uuid4().hex

    created = _required_mapping(
        client.request_json(
            "POST",
            "/api/upload-sessions",
            payload={
                "filename": "staging-smoke.txt",
                "sizeBytes": len(content),
                "mediaType": "text/plain",
                "sha256": digest_hex,
            },
            headers={"Idempotency-Key": f"staging-upload-{suffix}"},
            expected_statuses={201},
        ),
        "Upload creation response",
    )
    session_id = _required_str(created, "sessionId")
    progress.record_reference("upload_session_id", session_id)
    progress.complete_step("object_uploaded")
    session_path = f"/api/upload-sessions/{session_id}"
    presign = _required_mapping(
        client.request_json(
            "POST",
            f"{session_path}/parts/1/presign",
            payload={"sizeBytes": len(content), "checksumSha256": checksum_b64},
        ),
        "Part presign response",
    )
    signed_headers = presign.get("headers")
    if not isinstance(signed_headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in signed_headers.items()
    ):
        raise StagingSmokeFailure("Part presign response returned invalid headers.")
    etag = client.put_bytes(
        _required_str(presign, "url"),
        content=content,
        headers=signed_headers,
    )
    progress.complete_step("upload_completed")
    completed = _required_mapping(
        client.request_json(
            "POST",
            f"{session_path}/complete",
            payload={
                "parts": [
                    {
                        "partNumber": 1,
                        "sizeBytes": len(content),
                        "etag": etag,
                        "checksumSha256": checksum_b64,
                    }
                ]
            },
        ),
        "Upload completion response",
    )
    version_id = _required_str(completed, "versionId")
    progress.record_reference("document_version_id", version_id)
    progress.complete_step("document_ready")
    deadline = progress.started + timeout_seconds
    _wait_for_ready_version(
        client,
        version_id=version_id,
        deadline=deadline,
        monotonic=monotonic,
        sleep=sleep,
    )
    progress.complete_step("agent_run_created")
    run = _required_mapping(
        client.request_json(
            "POST",
            "/api/agent-runs",
            payload={
                "documentVersionId": version_id,
                "taskType": "question_answer",
                "inputText": "According to the document, what is the evidence retention period?",
                "publishRequested": False,
            },
            headers={"Idempotency-Key": f"staging-agent-{suffix}"},
            expected_statuses={200, 202},
        ),
        "Agent creation response",
    )
    run_id = _required_str(run, "runId")
    progress.record_reference("agent_run_id", run_id)
    progress.complete_step("agent_run_succeeded")
    terminal_status = _wait_for_run(
        client,
        run_id=run_id,
        deadline=deadline,
        monotonic=monotonic,
        sleep=sleep,
    )
    progress.agent_terminal_status = terminal_status
    if terminal_status != "succeeded":
        raise StagingSmokeFailure(
            f"Agent run ended with status {terminal_status}.", code="agent_terminal_status"
        )
    progress.complete_step("answer_artifact_listed")
    _validate_answer_artifact(client, run_id=run_id, version_id=version_id, progress=progress)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an authenticated staging upload, ingestion, and Agent smoke"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allowed-host", action="append", required=True)
    parser.add_argument("--allowed-object-store-host", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument(
        "--allow-loopback-http",
        action="store_true",
        help="allow explicit loopback-only HTTP endpoints for local recovery drills",
    )
    args = parser.parse_args()
    configuration_progress = _SmokeProgress(started=time.monotonic())
    token = os.environ.get("STAGING_SMOKE_TOKEN", "")
    try:
        if not token:
            raise StagingSmokeFailure(
                "STAGING_SMOKE_TOKEN is required", code="invalid_configuration"
            )
        report = run_staging_smoke(
            UrlLibSmokeClient(
                base_url=args.base_url,
                token=token,
                allowed_control_plane_hosts=tuple(args.allowed_host),
                allowed_object_store_hosts=tuple(args.allowed_object_store_host),
                allow_loopback_http=args.allow_loopback_http,
            ),
            timeout_seconds=args.timeout_seconds,
        )
    except StagingSmokeFailure as error:
        report = error.report or configuration_progress.report(
            time.monotonic,
            failure=StagingSmokeFailure(
                "Invalid staging smoke configuration.", code="invalid_configuration"
            ),
        )
    except Exception:
        report = configuration_progress.report(
            time.monotonic,
            failure=StagingSmokeFailure(
                "Unexpected staging smoke configuration failure.", code="unexpected_error"
            ),
        )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        try:
            args.report_path.parent.mkdir(parents=True, exist_ok=True)
            args.report_path.write_text(rendered, encoding="utf-8")
        except OSError:
            report["report_output"] = {"status": "failed", "code": "report_write_failed"}
            if report["status"] == "passed":
                report["status"] = "failed"
                report["failure"] = {
                    "step": "report_write",
                    "code": "report_write_failed",
                    "http_status": None,
                }
            rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
            print("Staging smoke report could not be written.", file=sys.stderr)
    print(rendered, end="")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
