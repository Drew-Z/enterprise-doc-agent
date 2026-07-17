from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "infra" / "compose" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
READY_COMPONENTS = {"database", "redis", "object_store"}


@dataclass(frozen=True, slots=True)
class PortCheck:
    name: str
    host: str
    port: int


@dataclass(slots=True)
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: TextIO


class SmokeFailure(RuntimeError):
    pass


PORTS = (
    PortCheck("web", "127.0.0.1", 5173),
    PortCheck("api", "127.0.0.1", 8000),
    PortCheck("worker", "127.0.0.1", 8081),
    PortCheck("postgres", "127.0.0.1", 5432),
    PortCheck("redis", "127.0.0.1", 6379),
    PortCheck("minio-api", "127.0.0.1", 9000),
    PortCheck("minio-console", "127.0.0.1", 9001),
)

APPLICATIONS = (
    ("api", ["uv", "run", "enterprise-doc-api"]),
    ("worker", ["uv", "run", "enterprise-doc-worker"]),
    ("web", ["pnpm", "--filter", "web", "dev"]),
)


def command_available(command: str) -> bool:
    return shutil.which(command) is not None


def port_is_free(check: PortCheck) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((check.host, check.port)) != 0


def wait_for_ports(checks: tuple[PortCheck, ...], timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = list(checks)
    while pending and time.monotonic() < deadline:
        pending = [check for check in pending if port_is_free(check)]
        if pending:
            time.sleep(0.5)
    if pending:
        names = ", ".join(f"{check.name}:{check.port}" for check in pending)
        raise SmokeFailure(f"Host ports did not become reachable: {names}")


def preflight() -> int:
    missing = [command for command in ("docker", "uv", "pnpm") if not command_available(command)]
    if missing:
        print(f"Missing commands: {', '.join(missing)}")
        return 1

    docker = subprocess.run(
        ["docker", "version"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if docker.returncode != 0:
        print("Docker daemon is unavailable")
        return 1

    occupied = [f"{item.name}:{item.port}" for item in PORTS if not port_is_free(item)]
    if occupied:
        print(f"Ports already in use: {', '.join(occupied)}")
        return 1

    print("Foundation preflight passed")
    return 0


def run_command(command: list[str]) -> None:
    print(f"Running: {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SmokeFailure(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )


def start_process(name: str, command: list[str], log_directory: Path) -> ManagedProcess:
    log_path = log_directory / f"{name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    resolved_command = resolve_process_command(command)
    process = subprocess.Popen(
        resolved_command,
        cwd=ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
        start_new_session=sys.platform != "win32",
    )
    print(f"Started {name} with PID {process.pid}")
    return ManagedProcess(
        name=name,
        process=process,
        log_path=log_path,
        log_handle=log_handle,
    )


def resolve_process_command(command: list[str]) -> list[str]:
    if command == ["uv", "run", "enterprise-doc-api"]:
        return [sys.executable, "-m", "enterprise_doc_api"]
    if command == ["uv", "run", "enterprise-doc-worker"]:
        return [sys.executable, "-m", "enterprise_doc_worker"]
    if command == ["pnpm", "--filter", "web", "dev"]:
        node = shutil.which("node")
        if node is None:
            raise SmokeFailure("Node.js executable is unavailable")
        vite = ROOT / "apps" / "web" / "node_modules" / "vite" / "bin" / "vite.js"
        return [
            node,
            str(vite),
            str(ROOT / "apps" / "web"),
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
        ]
    raise SmokeFailure(f"Unsupported managed process command: {' '.join(command)}")


def log_tail(process: ManagedProcess, lines: int = 40) -> str:
    process.log_handle.flush()
    content = process.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def assert_processes_running(processes: list[ManagedProcess]) -> None:
    stopped = [process for process in processes if process.process.poll() is not None]
    if stopped:
        details = "\n\n".join(
            f"{process.name} exited with {process.process.returncode}:\n{log_tail(process)}"
            for process in stopped
        )
        raise SmokeFailure(details)


def wait_for_http(
    url: str,
    *,
    processes: list[ManagedProcess],
    readiness: bool = False,
    timeout_seconds: float = 60,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        assert_processes_running(processes)
        try:
            with urlopen(url, timeout=2) as response:
                body = response.read()
                if response.status != 200:
                    last_error = f"HTTP {response.status}"
                elif readiness:
                    payload = json.loads(body)
                    checks = payload.get("checks", {})
                    if (
                        payload.get("status") == "ready"
                        and set(checks) == READY_COMPONENTS
                        and all(item.get("status") == "up" for item in checks.values())
                    ):
                        print(f"Ready: {url}")
                        return
                    last_error = f"invalid readiness payload: {payload}"
                else:
                    print(f"Available: {url}")
                    return
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise SmokeFailure(f"Timed out waiting for {url}: {last_error}")


def terminate_process(process: ManagedProcess) -> None:
    try:
        if process.process.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(process.process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            else:
                os.killpg(process.process.pid, signal.SIGTERM)
                try:
                    process.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.process.pid, signal.SIGKILL)
        process.process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Cleanup warning for {process.name}: {type(exc).__name__}")
    finally:
        process.log_handle.close()


def run_smoke() -> int:
    if preflight() != 0:
        return 1

    compose_started = False
    processes: list[ManagedProcess] = []
    smoke_passed = False
    log_directory = Path(tempfile.mkdtemp(prefix="enterprise-doc-agent-smoke-"))
    try:
        run_command([*COMPOSE, "up", "-d", "--wait"])
        compose_started = True
        wait_for_ports(
            tuple(check for check in PORTS if check.name not in {"api", "worker", "web"})
        )
        run_command([*COMPOSE, "--profile", "init", "run", "--rm", "minio-init"])
        run_command(["uv", "run", "alembic", "upgrade", "head"])

        for name, command in APPLICATIONS:
            processes.append(start_process(name, command, log_directory))
        wait_for_http(
            "http://127.0.0.1:8000/health/live",
            processes=processes,
        )
        wait_for_http(
            "http://127.0.0.1:8000/health/ready",
            processes=processes,
            readiness=True,
        )
        wait_for_http(
            "http://127.0.0.1:8081/health/live",
            processes=processes,
        )
        wait_for_http(
            "http://127.0.0.1:8081/health/ready",
            processes=processes,
            readiness=True,
        )
        wait_for_http(
            "http://127.0.0.1:5173/",
            processes=processes,
        )
        smoke_passed = True
        print("Foundation smoke passed")
        return 0
    except (OSError, SmokeFailure) as exc:
        print(f"Foundation smoke failed: {exc}")
        return 1
    finally:
        for process in reversed(processes):
            if process.process.poll() not in (None, 0):
                print(f"{process.name} log:\n{log_tail(process)}")
            terminate_process(process)
        if smoke_passed:
            shutil.rmtree(log_directory, ignore_errors=True)
        else:
            print(f"Smoke process logs retained at: {log_directory}")
        if compose_started:
            result = subprocess.run([*COMPOSE, "down"], cwd=ROOT, check=False)
            if result.returncode != 0:
                print("Cleanup warning: Docker Compose down failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the M0 local environment")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Check tools, Docker, and ports")
    mode.add_argument("--run", action="store_true", help="Run and clean up the complete M0 smoke")
    args = parser.parse_args()
    raise SystemExit(run_smoke() if args.run else preflight())


if __name__ == "__main__":
    main()
