from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from foundation_smoke import (
    COMPOSE,
    ROOT,
    ManagedProcess,
    PortCheck,
    SmokeFailure,
    command_available,
    log_tail,
    port_is_free,
    run_command,
    start_process,
    terminate_process,
    wait_for_http,
    wait_for_ports,
)

API_BASE_URL = "http://127.0.0.1:8000"
MIB = 1024**2
GENERATED_BYTE = b"a"
HASH_CHUNK_SIZE = MIB
SMOKE_PORTS = (
    PortCheck("api", "127.0.0.1", 8000),
    PortCheck("postgres", "127.0.0.1", 5432),
    PortCheck("redis", "127.0.0.1", 6379),
    PortCheck("minio-api", "127.0.0.1", 9000),
    PortCheck("minio-console", "127.0.0.1", 9001),
)


@dataclass(frozen=True, slots=True)
class ApiRssSample:
    generation: int
    elapsed_seconds: float
    rss_bytes: int


@dataclass(frozen=True, slots=True)
class UploadedPart:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256: str


class ApiRssSampler:
    def __init__(
        self,
        process: ManagedProcess,
        *,
        generation: int,
        enabled: bool,
        observed_pid: int,
    ) -> None:
        self.process = process
        self.generation = generation
        self.enabled = enabled
        self.observed_pid = observed_pid
        self.samples: list[ApiRssSample] = []
        self._started = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        if self.enabled:
            self._thread.start()

    def stop(self) -> tuple[ApiRssSample, ...]:
        if self.enabled:
            self._stop.set()
            self._thread.join(timeout=5)
        return tuple(self.samples)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                rss_bytes = read_process_rss_bytes(self.observed_pid)
            except OSError:
                if self.process.process.poll() is not None:
                    return
            else:
                self.samples.append(
                    ApiRssSample(
                        generation=self.generation,
                        elapsed_seconds=round(time.monotonic() - self._started, 3),
                        rss_bytes=rss_bytes,
                    )
                )
            self._stop.wait(0.25)


def deterministic_bytes(*, offset: int, size_bytes: int) -> bytes:
    if offset < 0 or size_bytes < 0:
        raise ValueError("Generated content ranges must be non-negative.")
    return GENERATED_BYTE * size_bytes


def sha256_for_generated_content(size_bytes: int) -> str:
    if size_bytes <= 0:
        raise ValueError("Generated content must not be empty.")
    digest = hashlib.sha256()
    remaining = size_bytes
    offset = 0
    while remaining:
        chunk_size = min(remaining, HASH_CHUNK_SIZE)
        digest.update(deterministic_bytes(offset=offset, size_bytes=chunk_size))
        offset += chunk_size
        remaining -= chunk_size
    return digest.hexdigest()


def read_process_rss_bytes(pid: int) -> int:
    if sys.platform == "win32":
        return _read_windows_rss_bytes(pid)

    status_path = Path(f"/proc/{pid}/status")
    if status_path.is_file():
        for line in status_path.read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                if len(fields) >= 2:
                    return int(fields[1]) * 1024
        raise OSError("The process RSS field is unavailable.")

    result = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise OSError("The process RSS value is unavailable.")
    return int(result.stdout.strip()) * 1024


def resolve_api_listener_pid(process: ManagedProcess) -> int:
    if sys.platform != "win32":
        return process.process.pid
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-NetTCPConnection -LocalPort 8000 -State Listen | "
            "Select-Object -First 1 -ExpandProperty OwningProcess)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        listener_pid = int(result.stdout.strip())
    except ValueError:
        return process.process.pid
    return listener_pid if listener_pid > 0 else process.process.pid


def _read_windows_rss_bytes(pid: int) -> int:
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    process_handle = kernel32.OpenProcess(
        query_limited_information,
        False,
        pid,
    )
    if not process_handle:
        raise OSError("The API process could not be opened for RSS sampling.")
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        succeeded = psapi.GetProcessMemoryInfo(
            process_handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not succeeded:
            raise OSError("The API process RSS value could not be sampled.")
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(process_handle)


def build_sanitized_report(
    *,
    size_bytes: int,
    part_size_bytes: int,
    expected_part_count: int,
    interrupted_after_parts: int,
    observed_parts_before_resume: int,
    uploaded_parts_after_resume: int,
    first_completion_replayed: bool,
    second_completion_replayed: bool,
    rss_samples: tuple[ApiRssSample, ...],
    started_at: str,
    completed_at: str,
    duration_seconds: float,
    environment: dict[str, str],
) -> dict[str, Any]:
    rss_values = [sample.rss_bytes for sample in rss_samples]
    generation_deltas: list[int] = []
    for generation in sorted({sample.generation for sample in rss_samples}):
        values = [sample.rss_bytes for sample in rss_samples if sample.generation == generation]
        if values:
            generation_deltas.append(max(values) - min(values))

    return {
        "schema_version": 1,
        "milestone": "M1",
        "result": "passed",
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": round(duration_seconds, 3),
        "environment": environment,
        "generated_content": {
            "size_bytes": size_bytes,
            "media_type": "text/plain",
            "materialized_on_client_disk": False,
        },
        "multipart": {
            "part_size_bytes": part_size_bytes,
            "expected_part_count": expected_part_count,
            "interrupted_after_parts": interrupted_after_parts,
            "observed_parts_before_resume": observed_parts_before_resume,
            "uploaded_parts_after_resume": uploaded_parts_after_resume,
        },
        "restart_count": 1,
        "first_completion_replayed": first_completion_replayed,
        "completion_retry_replayed": second_completion_replayed,
        "api_rss": {
            "measured": bool(rss_values),
            "sample_count": len(rss_values),
            "min_bytes": min(rss_values) if rss_values else None,
            "max_bytes": max(rss_values) if rss_values else None,
            "max_generation_delta_bytes": max(generation_deltas, default=0),
            "sampling_interval_seconds": 0.25,
        },
        "direct_transfer": {
            "control_plane": "FastAPI",
            "byte_plane": "client-to-object-store-presigned-put",
        },
        "limitations": [
            "This is one local execution on one machine, not a load test or a "
            "production capacity claim.",
            "RSS sampling observes the API process working set and does not prove "
            "allocator-level absence of every temporary allocation.",
            "The generated TXT content is deterministic synthetic data and does not "
            "represent document parsing or ingestion behavior.",
        ],
    }


def _preflight(*, size_bytes: int, interrupt_after_parts: int) -> None:
    missing = [command for command in ("docker", "uv") if not command_available(command)]
    if missing:
        raise SmokeFailure(f"Missing commands: {', '.join(missing)}")
    if size_bytes <= 0:
        raise SmokeFailure("The smoke size must be positive.")
    if interrupt_after_parts <= 0:
        raise SmokeFailure("The interruption point must be positive.")

    docker = subprocess.run(
        ["docker", "version"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if docker.returncode != 0:
        raise SmokeFailure("Docker daemon is unavailable.")
    occupied = [f"{item.name}:{item.port}" for item in SMOKE_PORTS if not port_is_free(item)]
    if occupied:
        raise SmokeFailure(f"Ports already in use: {', '.join(occupied)}")

    free_bytes = shutil.disk_usage(ROOT).free
    required_free_bytes = max(2 * 1024**3, size_bytes * 2)
    if free_bytes < required_free_bytes:
        raise SmokeFailure(
            "Insufficient host free space for the generated object-store smoke payload."
        )


def _bootstrap_token(size_bytes: int) -> str:
    suffix = uuid4().hex[:12]
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/bootstrap_local_principal.py",
            "--tenant-name",
            f"M1 Smoke {suffix}",
            "--tenant-slug",
            f"m1-smoke-{suffix}",
            "--email",
            f"m1-smoke-{suffix}@example.test",
            "--quota-bytes",
            str(max(10 * 1024**3, size_bytes * 2)),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SmokeFailure("The local principal bootstrap failed.")
    try:
        payload = json.loads(result.stdout)
        token = payload["token"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SmokeFailure("The local principal bootstrap returned an invalid payload.") from exc
    if not isinstance(token, str) or not token:
        raise SmokeFailure("The local principal bootstrap did not return a token.")
    return token


def _authorized_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json_response(response: httpx.Response, *, expected_statuses: set[int]) -> dict[str, Any]:
    if response.status_code not in expected_statuses:
        code = "unknown"
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("code"), str):
                    code = error["code"]
        except json.JSONDecodeError:
            pass
        raise SmokeFailure(
            f"A control-plane request failed with HTTP {response.status_code} ({code})."
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise SmokeFailure("A control-plane response was not a JSON object.")
    return payload


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SmokeFailure("A control-plane response omitted a required integer field.")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SmokeFailure("A control-plane response omitted a required string field.")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise SmokeFailure("A control-plane response omitted a required boolean field.")
    return value


def _create_session(client: httpx.Client, *, token: str, size_bytes: int) -> dict[str, Any]:
    print("Hashing deterministic generated content")
    declared_digest = sha256_for_generated_content(size_bytes)
    response = client.post(
        "/api/upload-sessions",
        headers={**_authorized_headers(token), "Idempotency-Key": f"m1-smoke-{uuid4().hex}"},
        json={
            "filename": "m1-smoke.txt",
            "sizeBytes": size_bytes,
            "mediaType": "text/plain",
            "sha256": declared_digest,
        },
    )
    return _json_response(response, expected_statuses={201})


def _part_size(*, part_number: int, total_size: int, part_size: int, part_count: int) -> int:
    if part_number < part_count:
        return part_size
    return total_size - part_size * (part_count - 1)


def _upload_part(
    client: httpx.Client,
    *,
    token: str,
    session_path: str,
    part_number: int,
    size_bytes: int,
    offset: int,
) -> UploadedPart:
    content = deterministic_bytes(offset=offset, size_bytes=size_bytes)
    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    presign = _json_response(
        client.post(
            f"{session_path}/parts/{part_number}/presign",
            headers=_authorized_headers(token),
            json={"sizeBytes": size_bytes, "checksumSha256": checksum},
        ),
        expected_statuses={200},
    )
    url = _required_str(presign, "url")
    signed_headers = presign.get("headers")
    if not isinstance(signed_headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in signed_headers.items()
    ):
        raise SmokeFailure("A presign response returned invalid signed headers.")
    put_response = httpx.put(
        url,
        headers={**signed_headers, "Content-Length": str(size_bytes)},
        content=content,
        timeout=httpx.Timeout(180.0, connect=10.0),
    )
    if put_response.status_code != 200:
        raise SmokeFailure(
            f"A direct object-store part transfer failed with HTTP {put_response.status_code}."
        )
    etag = put_response.headers.get("etag")
    if not etag:
        raise SmokeFailure("A direct object-store part transfer omitted its ETag.")
    return UploadedPart(
        part_number=part_number,
        size_bytes=size_bytes,
        etag=etag,
        checksum_sha256=checksum,
    )


def _observed_parts(payload: dict[str, Any]) -> tuple[UploadedPart, ...]:
    raw_parts = payload.get("uploadedParts")
    if not isinstance(raw_parts, list):
        raise SmokeFailure("The resume response omitted its observed parts.")
    parts: list[UploadedPart] = []
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise SmokeFailure("The resume response returned an invalid part.")
        parts.append(
            UploadedPart(
                part_number=_required_int(raw_part, "partNumber"),
                size_bytes=_required_int(raw_part, "sizeBytes"),
                etag=_required_str(raw_part, "etag"),
                checksum_sha256=_required_str(raw_part, "checksumSha256"),
            )
        )
    return tuple(sorted(parts, key=lambda item: item.part_number))


def _complete(
    client: httpx.Client,
    *,
    token: str,
    session_path: str,
    parts: tuple[UploadedPart, ...],
) -> dict[str, Any]:
    return _json_response(
        client.post(
            f"{session_path}/complete",
            headers=_authorized_headers(token),
            json={
                "parts": [
                    {
                        "partNumber": part.part_number,
                        "sizeBytes": part.size_bytes,
                        "etag": part.etag,
                        "checksumSha256": part.checksum_sha256,
                    }
                    for part in parts
                ]
            },
        ),
        expected_statuses={200},
    )


def _start_api(log_directory: Path, *, generation: int) -> ManagedProcess:
    process = start_process(
        f"api-generation-{generation}",
        ["uv", "run", "enterprise-doc-api"],
        log_directory,
    )
    wait_for_http(
        f"{API_BASE_URL}/health/ready",
        processes=[process],
        readiness=True,
        timeout_seconds=90,
    )
    return process


def run_smoke(args: argparse.Namespace) -> int:
    compose_started = False
    api_process: ManagedProcess | None = None
    sampler: ApiRssSampler | None = None
    all_samples: list[ApiRssSample] = []
    smoke_passed = False
    log_directory = Path(tempfile.mkdtemp(prefix="enterprise-doc-agent-m1-smoke-"))
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()

    try:
        _preflight(
            size_bytes=args.size_bytes,
            interrupt_after_parts=args.interrupt_after_parts,
        )
        run_command([*COMPOSE, "up", "-d", "--wait"])
        compose_started = True
        wait_for_ports(tuple(check for check in SMOKE_PORTS if check.name != "api"))
        run_command([*COMPOSE, "--profile", "init", "run", "--rm", "minio-init"])
        run_command(["uv", "run", "alembic", "upgrade", "head"])
        print("Bootstrapping an isolated local principal")
        token = _bootstrap_token(args.size_bytes)

        api_process = _start_api(log_directory, generation=1)
        sampler = ApiRssSampler(
            api_process,
            generation=1,
            enabled=args.measure_api_rss,
            observed_pid=resolve_api_listener_pid(api_process),
        )
        sampler.start()
        with httpx.Client(base_url=API_BASE_URL, timeout=30.0) as client:
            created = _create_session(client, token=token, size_bytes=args.size_bytes)
            session_identifier = _required_str(created, "sessionId")
            session_path = f"/api/upload-sessions/{session_identifier}"
            part_size_bytes = _required_int(created, "partSizeBytes")
            expected_part_count = _required_int(created, "expectedPartCount")
            if not 0 < args.interrupt_after_parts < expected_part_count:
                raise SmokeFailure(
                    "The interruption point must leave at least one part for resume."
                )

            for part_number in range(1, args.interrupt_after_parts + 1):
                size_bytes = _part_size(
                    part_number=part_number,
                    total_size=args.size_bytes,
                    part_size=part_size_bytes,
                    part_count=expected_part_count,
                )
                _upload_part(
                    client,
                    token=token,
                    session_path=session_path,
                    part_number=part_number,
                    size_bytes=size_bytes,
                    offset=(part_number - 1) * part_size_bytes,
                )
                print(f"Uploaded pre-interruption part {part_number}/{expected_part_count}")

        all_samples.extend(sampler.stop())
        sampler = None
        terminate_process(api_process)
        api_process = None
        print("Restarting the API before resume")

        api_process = _start_api(log_directory, generation=2)
        sampler = ApiRssSampler(
            api_process,
            generation=2,
            enabled=args.measure_api_rss,
            observed_pid=resolve_api_listener_pid(api_process),
        )
        sampler.start()
        with httpx.Client(base_url=API_BASE_URL, timeout=30.0) as client:
            resumed = _json_response(
                client.get(session_path, headers=_authorized_headers(token)),
                expected_statuses={200},
            )
            observed_before_resume = _observed_parts(resumed)
            if len(observed_before_resume) != args.interrupt_after_parts:
                raise SmokeFailure("Resume reconciliation observed an unexpected part count.")
            observed_numbers = {part.part_number for part in observed_before_resume}
            uploaded_after_resume = 0
            for part_number in range(1, expected_part_count + 1):
                if part_number in observed_numbers:
                    continue
                size_bytes = _part_size(
                    part_number=part_number,
                    total_size=args.size_bytes,
                    part_size=part_size_bytes,
                    part_count=expected_part_count,
                )
                _upload_part(
                    client,
                    token=token,
                    session_path=session_path,
                    part_number=part_number,
                    size_bytes=size_bytes,
                    offset=(part_number - 1) * part_size_bytes,
                )
                uploaded_after_resume += 1
                print(f"Uploaded resumed part {part_number}/{expected_part_count}")

            reconciled = _json_response(
                client.get(session_path, headers=_authorized_headers(token)),
                expected_statuses={200},
            )
            completed_parts = _observed_parts(reconciled)
            if len(completed_parts) != expected_part_count:
                raise SmokeFailure("Final reconciliation did not observe every part.")

            first_completion = _complete(
                client,
                token=token,
                session_path=session_path,
                parts=completed_parts,
            )
            second_completion = _complete(
                client,
                token=token,
                session_path=session_path,
                parts=completed_parts,
            )
            first_replayed = _required_bool(first_completion, "replayed")
            second_replayed = _required_bool(second_completion, "replayed")
            if first_replayed or not second_replayed:
                raise SmokeFailure("Completion retry did not satisfy the idempotency contract.")
            if _required_str(first_completion, "documentId") != _required_str(
                second_completion, "documentId"
            ) or _required_str(first_completion, "versionId") != _required_str(
                second_completion, "versionId"
            ):
                raise SmokeFailure("Completion retry returned a different durable result.")

        all_samples.extend(sampler.stop())
        sampler = None
        completed_at = datetime.now(UTC).isoformat()
        report = build_sanitized_report(
            size_bytes=args.size_bytes,
            part_size_bytes=part_size_bytes,
            expected_part_count=expected_part_count,
            interrupted_after_parts=args.interrupt_after_parts,
            observed_parts_before_resume=len(observed_before_resume),
            uploaded_parts_after_resume=uploaded_after_resume,
            first_completion_replayed=first_replayed,
            second_completion_replayed=second_replayed,
            rss_samples=tuple(all_samples),
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=time.monotonic() - started,
            environment={
                "operating_system": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
            },
        )
        if args.measure_api_rss and not report["api_rss"]["measured"]:
            raise SmokeFailure("API RSS measurement was requested but produced no samples.")
        if args.report_path is not None:
            report_path = Path(args.report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print("Wrote sanitized multipart smoke report")
        print("M1 multipart smoke passed")
        smoke_passed = True
        return 0
    except (OSError, SmokeFailure, httpx.HTTPError) as exc:
        print(f"M1 multipart smoke failed: {exc}")
        return 1
    finally:
        if sampler is not None:
            all_samples.extend(sampler.stop())
        if api_process is not None:
            if api_process.process.poll() not in (None, 0):
                print(f"API log:\n{log_tail(api_process)}")
            terminate_process(api_process)
        if smoke_passed:
            shutil.rmtree(log_directory, ignore_errors=True)
        else:
            print(f"Smoke process logs retained at: {log_directory}")
        if compose_started:
            result = subprocess.run([*COMPOSE, "down"], cwd=ROOT, check=False)
            if result.returncode != 0:
                print("Cleanup warning: Docker Compose down failed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a restart/resume multipart upload against local PostgreSQL and MinIO"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Check tools, ports, and space")
    mode.add_argument("--run", action="store_true", help="Run the complete M1 multipart smoke")
    parser.add_argument("--size-bytes", type=int, default=1024**3)
    parser.add_argument("--interrupt-after-parts", type=int, default=2)
    parser.add_argument("--measure-api-rss", action="store_true")
    parser.add_argument("--report-path")
    args = parser.parse_args()
    if args.preflight:
        try:
            _preflight(
                size_bytes=args.size_bytes,
                interrupt_after_parts=args.interrupt_after_parts,
            )
        except SmokeFailure as exc:
            print(f"M1 multipart preflight failed: {exc}")
            raise SystemExit(1) from exc
        print("M1 multipart preflight passed")
        raise SystemExit(0)
    raise SystemExit(run_smoke(args))


if __name__ == "__main__":
    main()
