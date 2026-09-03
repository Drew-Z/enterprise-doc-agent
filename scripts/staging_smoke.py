from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class StagingSmokeFailure(RuntimeError):
    pass


_STAGING_SMOKE_USER_AGENT = "enterprise-doc-staging-smoke/1.0"


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
                        f"Control-plane request returned HTTP {response.status}."
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
                except (OSError, json.JSONDecodeError) as decode_error:
                    raise StagingSmokeFailure(
                        f"Control-plane request returned HTTP {error.code} without valid JSON."
                    ) from decode_error
                if not isinstance(decoded, (dict, list)):
                    raise StagingSmokeFailure(
                        "Control-plane response was not a JSON object or list."
                    ) from None
                return decoded
            raise StagingSmokeFailure(
                f"Control-plane request returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise StagingSmokeFailure(
                f"Control-plane request failed with {type(error).__name__}."
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
                        f"Direct object-store upload returned HTTP {response.status}."
                    )
                etag_value = response.headers.get("ETag")
        except HTTPError as error:
            raise StagingSmokeFailure(
                f"Direct object-store upload returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError) as error:
            raise StagingSmokeFailure(
                f"Direct object-store upload failed with {type(error).__name__}."
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
                        f"Direct object-store download returned HTTP {response.status}."
                    )
                content = response.read()
        except HTTPError as error:
            raise StagingSmokeFailure(
                f"Direct object-store download returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError) as error:
            raise StagingSmokeFailure(
                f"Direct object-store download failed with {type(error).__name__}."
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
    download = _required_mapping(
        client.request_json("GET", f"/api/agent-artifacts/{artifact_id}/download"),
        "Agent artifact download response",
    )
    if _required_str(download, "contentSha256") != expected_sha256:
        raise StagingSmokeFailure("Artifact download metadata changed its SHA-256.")
    if _required_int(download, "sizeBytes") != expected_size:
        raise StagingSmokeFailure("Artifact download metadata changed its byte size.")
    body = client.get_bytes(_required_str(download, "url"))
    if len(body) != expected_size:
        raise StagingSmokeFailure("Downloaded artifact byte size did not match metadata.")
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise StagingSmokeFailure("Downloaded artifact SHA-256 did not match metadata.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
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
        if any(item.get("versionId") == version_id for item in payload):
            return
        sleep(2.0)
    raise StagingSmokeFailure("Document ingestion did not reach ready before the timeout.")


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
    raise StagingSmokeFailure("Agent run did not reach a terminal status before the timeout.")


def run_staging_smoke(
    client: SmokeClient,
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    started = monotonic()
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
    deadline = started + timeout_seconds
    _wait_for_ready_version(
        client,
        version_id=version_id,
        deadline=deadline,
        monotonic=monotonic,
        sleep=sleep,
    )
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
    terminal_status = _wait_for_run(
        client,
        run_id=run_id,
        deadline=deadline,
        monotonic=monotonic,
        sleep=sleep,
    )
    if terminal_status != "succeeded":
        raise StagingSmokeFailure(f"Agent run ended with status {terminal_status}.")
    _validate_answer_artifact(client, run_id=run_id, version_id=version_id)

    completed_at = datetime.now(UTC)
    return {
        "schema_version": 1,
        "scenario": "authenticated-upload-ingestion-agent",
        "status": "passed",
        "steps": [
            "upload_session_created",
            "object_uploaded",
            "upload_completed",
            "document_ready",
            "agent_run_created",
            "agent_run_succeeded",
            "answer_artifact_listed",
            "answer_artifact_downloaded",
            "answer_artifact_sha256_verified",
            "answer_citation_verified",
        ],
        "sample_count": 1,
        "duration_seconds": max(0.0, monotonic() - started),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
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
        ],
    }


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
    token = os.environ.get("STAGING_SMOKE_TOKEN", "")
    if not token:
        raise SystemExit("STAGING_SMOKE_TOKEN is required")
    if args.timeout_seconds <= 0:
        raise SystemExit("timeout must be positive")
    try:
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
        print(f"Staging smoke failed: {error}")
        raise SystemExit(1) from error
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
