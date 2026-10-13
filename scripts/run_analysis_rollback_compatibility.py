"""Exercise an older signed runtime against the current analysis schema.

The check is intentionally isolated from the staging cluster.  It starts only
ephemeral Docker resources, migrates a fresh PostgreSQL database with the
candidate API image, and then probes the previous four workload images against
that schema.  The report contains release identities and probe results, never
the generated credentials or raw container output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_HEAD = "20261012_0043"
ANALYSIS_TABLE = "analysis_projects"
SERVICES = ("api", "worker", "consumer", "web")
IMAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
DEFAULT_IMAGES = {
    "postgres": (
        "pgvector/pgvector@sha256:131dcf7ff6a900545df8e7e092c270aa8c6db2f2c818e408cb45ec21316b74e6"
    ),
    "redis": ("redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"),
    "minio": (
        "minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
    ),
    "minio_mc": (
        "minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
    ),
}
DEFAULT_TIMEOUT_SECONDS = 180.0


class CompatibilityError(RuntimeError):
    """Raised when the isolated compatibility check cannot be completed."""


@dataclass(frozen=True, slots=True)
class CompatibilityConfig:
    candidate_api: str
    rollback_images: Mapping[str, str]
    dependency_images: Mapping[str, str]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def validate(self) -> None:
        validate_image_ref(self.candidate_api, "candidate_api")
        missing = set(SERVICES) - set(self.rollback_images)
        if missing:
            raise CompatibilityError("rollback image refs missing: " + ", ".join(sorted(missing)))
        for service in SERVICES:
            validate_image_ref(self.rollback_images[service], f"rollback_{service}")
        for name in ("postgres", "redis", "minio", "minio_mc"):
            validate_image_ref(self.dependency_images[name], f"dependency_{name}")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 900:
            raise CompatibilityError("timeout_seconds must be between 0 and 900")


def validate_image_ref(value: object, label: str) -> str:
    if not isinstance(value, str) or IMAGE_PATTERN.fullmatch(value.strip()) is None:
        raise CompatibilityError(f"{label} must be an immutable image@sha256 reference")
    return value.strip()


def _safe_token(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _database_url(password: str) -> str:
    return f"postgresql+psycopg://compat_user:{password}@postgres:5432/compat_db"


def build_runtime_environment(*, password: str) -> dict[str, str]:
    """Build one env file payload shared by dependency and workload containers."""
    return {
        "APP_ENV": "local",
        "LOG_LEVEL": "WARNING",
        "POSTGRES_USER": "compat_user",
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": "compat_db",
        "MINIO_ROOT_USER": "compat_minio",
        "MINIO_ROOT_PASSWORD": password,
        "DATABASE__URL": _database_url(password),
        "REDIS__URL": "redis://redis:6379/0",
        "OBJECT_STORE__ENDPOINT": "http://minio:9000",
        "OBJECT_STORE__PRESIGN_ENDPOINT": "http://minio:9000",
        "OBJECT_STORE__ACCESS_KEY": "compat_minio",
        "OBJECT_STORE__SECRET_KEY": password,
        "OBJECT_STORE__DOCUMENTS_BUCKET": "documents",
        "OBJECT_STORE__ARTIFACTS_BUCKET": "artifacts",
        "OBJECT_STORE__SECURE": "false",
        "UPLOAD__STRUCTURED_DOCUMENTS_ENABLED": "false",
        "ANALYSIS__EXECUTION_ENABLED": "false",
        "ANALYSIS__UNIT_DEADLINE_SECONDS": "120",
        "ANALYSIS__RUN_DEADLINE_SECONDS": "3600",
        "ANALYSIS__MAX_INPUT_BYTES": "65536",
        "MODEL__PROVIDER": "deterministic",
        "MODEL__ROUTE_ID": "compatibility",
        "EMBEDDING__PROVIDER": "hash",
        "OTEL__ENABLED": "false",
        "OTEL__METRICS_ENABLED": "true",
        "API__HOST": "0.0.0.0",
        "API__PORT": "8000",
        "WORKER__HOST": "0.0.0.0",
        "WORKER__PROBE_PORT": "8081",
        "WORKER__CONSUMER_METRICS_PORT": "8082",
    }


def write_env_file(directory: Path, values: Mapping[str, str]) -> Path:
    path = directory / "runtime.env"
    # Docker's env-file format does not need shell escaping for these generated
    # values; reject line breaks so a future setting cannot inject another key.
    if any(
        "\n" in key or "\r" in key or "\n" in value or "\r" in value
        for key, value in values.items()
    ):
        raise CompatibilityError("runtime environment contains a line break")
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows runners do not expose POSIX mode bits; Docker still reads the
        # file before this temporary directory is removed.
        pass
    return path


class Docker:
    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["docker", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CompatibilityError(f"docker command failed: {type(error).__name__}") from error
        if check and result.returncode != 0:
            raise CompatibilityError(f"docker command exited with {result.returncode}")
        return result

    def pull(self, image: str) -> None:
        self.run(["pull", image], timeout_seconds=max(self.timeout_seconds, 600))

    def container(
        self, args: Sequence[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self.run(args, check=check)


def _env_args(env_file: Path) -> list[str]:
    return ["--env-file", str(env_file)]


def dependency_args(
    *,
    name: str,
    image: str,
    network: str,
    env_file: Path,
    container_name: str,
) -> list[str]:
    common = [
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        network,
        "--network-alias",
        name,
        "--label",
        "enterprise-doc-rollback-compatibility=true",
        *_env_args(env_file),
    ]
    if name == "postgres":
        return [
            *common,
            "--health-cmd",
            'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            "--health-interval",
            "2s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "40",
            image,
        ]
    if name == "redis":
        return [
            *common,
            "--health-cmd",
            "redis-cli ping",
            "--health-interval",
            "2s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "40",
            image,
        ]
    if name == "minio":
        return [
            *common,
            "--health-cmd",
            "curl -f http://127.0.0.1:9000/minio/health/live",
            "--health-interval",
            "2s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "40",
            image,
            "server",
            "/data",
            "--console-address",
            ":9001",
        ]
    raise CompatibilityError(f"unsupported dependency: {name}")


def init_minio_args(*, image: str, network: str, env_file: Path) -> list[str]:
    command = (
        "set -eu; "
        'mc alias set compat http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; '
        "mc mb --ignore-existing compat/documents; "
        "mc mb --ignore-existing compat/artifacts; "
        "mc anonymous set private compat/documents; "
        "mc anonymous set private compat/artifacts"
    )
    return [
        "run",
        "--rm",
        "--name",
        _safe_token("eda-compat-minio-init"),
        "--network",
        network,
        *_env_args(env_file),
        "--entrypoint",
        "/bin/sh",
        image,
        "-ec",
        command,
    ]


def migration_args(*, image: str, network: str, env_file: Path) -> list[str]:
    command = (
        "set -eu; "
        "alembic upgrade head; "
        "enterprise-doc-checkpointer-setup --setup; "
        "enterprise-doc-checkpointer-setup --check"
    )
    return [
        "run",
        "--rm",
        "--name",
        _safe_token("eda-compat-migration"),
        "--network",
        network,
        *_env_args(env_file),
        image,
        "sh",
        "-ec",
        command,
    ]


def schema_probe_args(*, image: str, network: str, env_file: Path) -> list[str]:
    code = (
        "import json, os, psycopg; "
        "dsn=os.environ['DATABASE__URL'].replace('postgresql+psycopg://','postgresql://',1); "
        "conn=psycopg.connect(dsn); "
        "cur=conn.cursor(); "
        'cur.execute("select version_num from alembic_version"); head=cur.fetchone()[0]; '
        "cur.execute(\"select to_regclass('public.analysis_projects') is not null\"); "
        "table=cur.fetchone()[0]; "
        "print(json.dumps({'head': head, 'analysis_projects': table}, sort_keys=True)); "
        "cur.close(); conn.close()"
    )
    return [
        "run",
        "--rm",
        "--name",
        _safe_token("eda-compat-schema-probe"),
        "--network",
        network,
        *_env_args(env_file),
        image,
        "python",
        "-c",
        code,
    ]


def workload_args(
    *,
    service: str,
    image: str,
    network: str,
    env_file: Path,
    host_port: int,
) -> list[str]:
    ports = {
        "api": (host_port, 8000),
        "worker": (host_port, 8081),
        "consumer": (host_port, 8082),
        "web": (host_port, 8080),
    }
    if service not in ports:
        raise CompatibilityError(f"unsupported workload: {service}")
    source, target = ports[service]
    args = [
        "run",
        "-d",
        "--name",
        _safe_token(f"eda-compat-{service}"),
        "--network",
        network,
        "--network-alias",
        f"enterprise-doc-{service}",
        "-p",
        f"127.0.0.1:{source}:{target}",
        "--label",
        "enterprise-doc-rollback-compatibility=true",
        *_env_args(env_file),
        image,
    ]
    if service == "web":
        args.extend(["nginx", "-g", "daemon off;"])
    return args


def _json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise CompatibilityError("schema probe did not return a JSON object")


def _container_health(docker: Docker, name: str) -> str:
    result = docker.run(
        [
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            name,
        ],
        check=False,
    )
    return result.stdout.strip()


def wait_container_healthy(docker: Docker, name: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _container_health(docker, name)
        if state == "healthy" or state == "running":
            return
        if state in {"exited", "dead"}:
            raise CompatibilityError(f"container {name} stopped before becoming healthy")
        time.sleep(2)
    raise CompatibilityError(f"container {name} did not become healthy")


def http_probe(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            body = response.read(4096)
    except urllib.error.HTTPError as error:
        return {"status": "failed", "http_status": int(error.code)}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "unreachable"}
    result: dict[str, Any] = {
        "status": "passed" if status == 200 else "failed",
        "http_status": status,
    }
    if url.endswith("/openapi.json") and status == 200:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["status"] = "failed"
        else:
            result["openapi_document"] = isinstance(payload, dict) and bool(payload.get("openapi"))
            result["analysis_route_present"] = isinstance(payload, dict) and any(
                str(path).startswith("/api/analysis") for path in payload.get("paths", {})
            )
            if not result["openapi_document"]:
                result["status"] = "failed"
    return result


def tcp_probe(host: str, port: int, *, timeout_seconds: float) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"status": "passed", "port": port}
    except (OSError, TimeoutError):
        return {"status": "unreachable", "port": port}


def wait_probe(
    probe: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: float,
    label: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {"status": "unreachable"}
    while time.monotonic() < deadline:
        last = probe()
        if last.get("status") == "passed":
            return last
        time.sleep(2)
    raise CompatibilityError(f"{label} probe did not pass")


def _pull_images(docker: Docker, config: CompatibilityConfig) -> None:
    refs = [
        config.candidate_api,
        *config.rollback_images.values(),
        *config.dependency_images.values(),
    ]
    for image in dict.fromkeys(refs):
        docker.pull(image)


def run_compatibility(config: CompatibilityConfig, *, pull_images: bool = True) -> dict[str, Any]:
    config.validate()
    docker = Docker(timeout_seconds=config.timeout_seconds)
    started_at = _utc_now()
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "candidate_api_image": config.candidate_api,
        "rollback_images": dict(config.rollback_images),
        "schema": {"expected_head": SCHEMA_HEAD, "analysis_table": ANALYSIS_TABLE},
        "checks": {},
        "cleanup": {"status": "not_started", "containers_removed": 0, "network_removed": False},
        "started_at": started_at,
    }
    created: list[str] = []
    network = _safe_token("eda-rollback-compat")
    try:
        if pull_images:
            _pull_images(docker, config)
        docker.run(
            [
                "network",
                "create",
                "--label",
                "enterprise-doc-rollback-compatibility=true",
                network,
            ]
        )
        password = secrets.token_urlsafe(24)
        with tempfile.TemporaryDirectory(prefix="enterprise-doc-rollback-compat-") as temp:
            env_file = write_env_file(Path(temp), build_runtime_environment(password=password))
            for name in ("postgres", "redis", "minio"):
                container_name = _safe_token(f"eda-compat-{name}")
                docker.container(
                    dependency_args(
                        name=name,
                        image=config.dependency_images[name],
                        network=network,
                        env_file=env_file,
                        container_name=container_name,
                    )
                )
                created.append(container_name)
                wait_container_healthy(
                    docker, container_name, timeout_seconds=config.timeout_seconds
                )

            docker.container(
                init_minio_args(
                    image=config.dependency_images["minio_mc"],
                    network=network,
                    env_file=env_file,
                )
            )
            docker.container(
                migration_args(
                    image=config.candidate_api,
                    network=network,
                    env_file=env_file,
                )
            )
            schema_result = docker.container(
                schema_probe_args(image=config.candidate_api, network=network, env_file=env_file)
            )
            schema = _json_line(schema_result.stdout)
            report["checks"]["schema"] = {
                "status": "passed"
                if schema.get("head") == SCHEMA_HEAD and schema.get("analysis_projects") is True
                else "failed",
                "head": schema.get("head"),
                "analysis_projects": schema.get("analysis_projects"),
            }
            if report["checks"]["schema"]["status"] != "passed":
                raise CompatibilityError("migrated schema did not match the expected analysis head")

            ports = {service: _free_port() for service in SERVICES}
            for service in SERVICES:
                container_result = docker.container(
                    workload_args(
                        service=service,
                        image=config.rollback_images[service],
                        network=network,
                        env_file=env_file,
                        host_port=ports[service],
                    )
                )
                container_name = container_result.stdout.strip()
                if not container_name:
                    raise CompatibilityError(f"{service} container did not return an id")
                created.append(container_name)

            api_url = f"http://127.0.0.1:{ports['api']}"
            worker_url = f"http://127.0.0.1:{ports['worker']}"
            web_url = f"http://127.0.0.1:{ports['web']}"
            report["checks"]["api_live"] = wait_probe(
                lambda: http_probe(f"{api_url}/health/live", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback API liveness",
            )
            report["checks"]["api_ready"] = wait_probe(
                lambda: http_probe(f"{api_url}/health/ready", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback API readiness",
            )
            report["checks"]["api_openapi"] = wait_probe(
                lambda: http_probe(f"{api_url}/openapi.json", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback API OpenAPI",
            )
            report["checks"]["worker_live"] = wait_probe(
                lambda: http_probe(f"{worker_url}/health/live", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback Worker liveness",
            )
            report["checks"]["worker_ready"] = wait_probe(
                lambda: http_probe(f"{worker_url}/health/ready", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback Worker readiness",
            )
            report["checks"]["consumer_port"] = wait_probe(
                lambda: tcp_probe("127.0.0.1", ports["consumer"], timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback Consumer metrics port",
            )
            report["checks"]["web_live"] = wait_probe(
                lambda: http_probe(f"{web_url}/health/live", timeout_seconds=5),
                timeout_seconds=config.timeout_seconds,
                label="rollback Web liveness",
            )
            report["ports"] = ports
            report["status"] = "passed"
    except CompatibilityError as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    except Exception as error:  # pragma: no cover - defensive CI boundary
        report["failure"] = {
            "type": type(error).__name__,
            "message": "unexpected compatibility failure",
        }
    finally:
        cleanup_errors: list[str] = []
        for name in reversed(created):
            result = docker.run(["rm", "-f", name], check=False)
            if result.returncode == 0:
                report["cleanup"]["containers_removed"] += 1
            else:
                cleanup_errors.append("container_remove_failed")
        network_result = docker.run(["network", "rm", network], check=False)
        report["cleanup"]["network_removed"] = network_result.returncode == 0
        if network_result.returncode != 0:
            cleanup_errors.append("network_remove_failed")
        report["cleanup"]["status"] = "passed" if not cleanup_errors else "failed"
        if cleanup_errors:
            report["cleanup"]["errors"] = cleanup_errors
            report["status"] = "failed"
        report["completed_at"] = _utc_now()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify signed rollback images against analysis schema 0043"
    )
    parser.add_argument("--candidate-api", required=True)
    for service in SERVICES:
        parser.add_argument(f"--rollback-{service}", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = CompatibilityConfig(
        candidate_api=args.candidate_api,
        rollback_images={service: getattr(args, f"rollback_{service}") for service in SERVICES},
        dependency_images=DEFAULT_IMAGES,
        timeout_seconds=args.timeout_seconds,
    )
    report = run_compatibility(config)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
