from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts.run_analysis_rollback_compatibility import (
    ANALYSIS_TABLE,
    DEFAULT_IMAGES,
    MAX_OPENAPI_BYTES,
    SCHEMA_HEAD,
    CompatibilityConfig,
    CompatibilityError,
    Docker,
    build_parser,
    build_runtime_environment,
    dependency_args,
    http_probe,
    migration_args,
    run_compatibility,
    schema_probe_args,
    validate_image_ref,
    wait_probe,
    write_env_file,
)


def _image(char: str) -> str:
    return f"ghcr.io/example/enterprise-doc-{char}@sha256:{char * 64}"


def test_config_requires_all_immutable_workload_and_dependency_refs() -> None:
    config = CompatibilityConfig(
        candidate_api=_image("a"),
        rollback_images={
            service: _image(char)
            for service, char in zip(
                ("api", "worker", "consumer", "web"), ("a", "b", "c", "d"), strict=True
            )
        },
        dependency_images={
            "postgres": _image("a"),
            "redis": _image("b"),
            "minio": _image("c"),
            "minio_mc": _image("d"),
        },
    )
    config.validate()

    with pytest.raises(CompatibilityError, match="immutable"):
        validate_image_ref("latest", "image")
    with pytest.raises(CompatibilityError, match="missing"):
        CompatibilityConfig(
            candidate_api=_image("a"),
            rollback_images={"api": _image("a")},
            dependency_images=config.dependency_images,
        ).validate()


def test_runtime_env_is_local_dark_and_contains_no_line_break_injection() -> None:
    values = build_runtime_environment(password="synthetic-password")

    assert values["APP_ENV"] == "local"
    assert values["UPLOAD__STRUCTURED_DOCUMENTS_ENABLED"] == "false"
    assert values["ANALYSIS__EXECUTION_ENABLED"] == "false"
    assert values["DATABASE__URL"].startswith("postgresql+psycopg://compat_user:")
    assert all("\n" not in value and "\r" not in value for value in values.values())


def test_env_file_is_private_and_round_trips_sorted_values(tmp_path: Path) -> None:
    path = write_env_file(tmp_path, {"B": "two", "A": "one"})
    assert path.read_text(encoding="utf-8") == "A=one\nB=two\n"
    with pytest.raises(CompatibilityError, match="line break"):
        write_env_file(tmp_path, {"BAD": "value\nINJECTED=yes"})


def test_migration_and_schema_probe_bind_candidate_image_and_database_network() -> None:
    env_file = Path("/tmp/runtime.env")
    migration = migration_args(image=_image("a"), network="compat-net", env_file=env_file)
    probe = schema_probe_args(image=_image("a"), network="compat-net", env_file=env_file)

    assert migration[0:2] == ["run", "--rm"]
    assert "alembic upgrade head" in migration[-1]
    assert "enterprise-doc-checkpointer-setup --check" in migration[-1]
    assert probe[-3:] == ["python", "-c", probe[-1]]
    assert "analysis_projects" in probe[-1]
    assert "compat-net" in migration and "compat-net" in probe


def test_dependency_health_command_does_not_embed_generated_password(tmp_path: Path) -> None:
    args = dependency_args(
        name="postgres",
        image=_image("p"),
        network="compat-net",
        env_file=tmp_path / "runtime.env",
        container_name="compat-postgres",
    )
    rendered = " ".join(args)
    assert "pg_isready" in rendered
    assert "synthetic-password" not in rendered
    assert "--health-retries" in args


def test_docker_failure_keeps_redacted_registry_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        returncode = 1
        stderr = "denied: password=secret-value; registry timeout"

    monkeypatch.setattr(
        "scripts.run_analysis_rollback_compatibility.subprocess.run",
        lambda *args, **kwargs: Result(),
    )

    with pytest.raises(CompatibilityError) as raised:
        Docker(timeout_seconds=1).run(["pull", "ghcr.io/example/image@sha256:" + "a" * 64])

    message = str(raised.value)
    assert "registry timeout" in message
    assert "password=<redacted>" in message
    assert "secret-value" not in message


def test_wait_probe_retries_until_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(({"status": "unreachable"}, {"status": "passed", "http_status": 200}))
    monkeypatch.setattr("scripts.run_analysis_rollback_compatibility.time.sleep", lambda _: None)
    observed = wait_probe(lambda: next(values), timeout_seconds=1, label="test")
    assert observed == {"status": "passed", "http_status": 200}


@pytest.mark.parametrize("pull_images", [True, False])
def test_early_failure_preserves_diagnostic_without_removing_uncreated_network(
    monkeypatch: pytest.MonkeyPatch, pull_images: bool
) -> None:
    config = CompatibilityConfig(
        candidate_api=_image("a"),
        rollback_images={service: _image("b") for service in ("api", "worker", "consumer", "web")},
        dependency_images={
            name: _image("c") for name in ("postgres", "redis", "minio", "minio_mc")
        },
    )
    commands: list[list[str]] = []

    def fail_command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, "", "access denied")

    monkeypatch.setattr("scripts.run_analysis_rollback_compatibility.subprocess.run", fail_command)
    report = run_compatibility(config, pull_images=pull_images)

    assert report["status"] == "failed"
    assert "access denied" in report["failure"]["message"]
    if pull_images:
        assert config.candidate_api in report["failure"]["message"]
    assert report["checks"] == {}
    assert report["cleanup"] == {
        "status": "passed",
        "containers_removed": 0,
        "network_created": False,
        "network_removed": False,
    }
    assert len(commands) == 1
    assert report["completed_at"]


def test_default_dependencies_build_pinned_binaries_and_report_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CompatibilityConfig(
        candidate_api=_image("a"),
        rollback_images={service: _image("b") for service in ("api", "worker", "consumer", "web")},
        dependency_images=DEFAULT_IMAGES,
    )
    builds: list[list[str]] = []

    def command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[1] == "build":
            builds.append(args)
            if args[args.index("--target") + 1] == "minio":
                return subprocess.CompletedProcess(args, 0, "sha256:" + "c" * 64 + "\n", "")
            return subprocess.CompletedProcess(args, 1, "", "checksum mismatch")
        if args[1] == "pull":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError("No runtime resources should start after a failed build")

    monkeypatch.setattr("scripts.run_analysis_rollback_compatibility.subprocess.run", command)
    report = run_compatibility(config)

    assert len(builds) == 2
    assert all("linux/amd64" in args for args in builds)
    assert report["status"] == "failed"
    assert "checksum mismatch" in report["failure"]["message"]
    assert report["dependency_builds"]["minio"]["image_id"] == "sha256:" + "c" * 64
    assert len(report["dependency_builds"]["minio"]["dockerfile_sha256"]) == 64
    assert report["cleanup"]["status"] == "passed"
    assert report["checks"] == {}


@pytest.mark.parametrize("description_size", [10_000, MAX_OPENAPI_BYTES])
def test_http_probe_reports_openapi_contract_without_returning_body(
    monkeypatch: pytest.MonkeyPatch,
    description_size: int,
) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return json.dumps(
                {
                    "openapi": "3.1.0",
                    "info": {"description": "x" * description_size},
                    "paths": {"/api/analysis/projects": {}},
                }
            ).encode()[:size]

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Response(),
    )
    result = http_probe("http://127.0.0.1/openapi.json", timeout_seconds=1)
    if description_size == MAX_OPENAPI_BYTES:
        assert result == {"status": "failed", "http_status": 200}
        return
    assert result == {
        "status": "passed",
        "http_status": 200,
        "openapi_document": True,
        "analysis_route_present": True,
    }
    assert "body" not in result


def test_schema_contract_defaults_preserve_the_historical_rehearsal() -> None:
    assert SCHEMA_HEAD == "20261012_0043"
    assert ANALYSIS_TABLE == "analysis_projects"


@pytest.mark.parametrize("revision", ["head", "", "20261104_0044\n", "20261104_0044;drop"])
def test_schema_selection_rejects_aliases_and_invalid_revisions(revision: str) -> None:
    config = CompatibilityConfig(
        candidate_api=_image("a"),
        rollback_images={service: _image("b") for service in ("api", "worker", "consumer", "web")},
        dependency_images=DEFAULT_IMAGES,
        expected_schema_head=revision,
    )
    with pytest.raises(CompatibilityError, match="exact"):
        config.validate()


@pytest.mark.parametrize(
    ("observed_head", "expected_head", "passed"),
    [
        ("20261104_0044", "20261104_0044", True),
        ("20261012_0043", "20261104_0044", False),
        ("20261104_0044", "20261012_0043", False),
    ],
)
def test_selected_schema_controls_runtime_admission_and_report(
    monkeypatch: pytest.MonkeyPatch, observed_head: str, expected_head: str, passed: bool
) -> None:
    config = CompatibilityConfig(
        candidate_api=_image("a"),
        rollback_images={service: _image("b") for service in ("api", "worker", "consumer", "web")},
        dependency_images={name: _image("c") for name in DEFAULT_IMAGES},
        expected_schema_head=expected_head,
    )
    workloads: list[list[str]] = []

    def command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = "container-id"
        if "python" in args and "-c" in args:
            output = json.dumps({"head": observed_head, "analysis_projects": True})
        if args[1] == "run" and "-p" in args:
            workloads.append(args)
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr("scripts.run_analysis_rollback_compatibility.subprocess.run", command)
    monkeypatch.setattr(
        "scripts.run_analysis_rollback_compatibility.wait_container_healthy", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "scripts.run_analysis_rollback_compatibility.wait_probe",
        lambda *a, **k: {"status": "passed"},
    )
    report = run_compatibility(config, pull_images=False)

    assert report["schema"]["expected_head"] == expected_head
    assert report["checks"]["schema"]["head"] == observed_head
    assert report["status"] == ("passed" if passed else "failed")
    assert len(workloads) == (4 if passed else 0)
    assert report["cleanup"]["status"] == "passed"
    assert report["cleanup"]["containers_removed"] == (7 if passed else 3)


def test_cli_accepts_explicit_0044_without_changing_historical_default() -> None:
    args = ["--candidate-api", _image("a")]
    for service in ("api", "worker", "consumer", "web"):
        args.extend([f"--rollback-{service}", _image("b")])
    assert build_parser().parse_args(args).expected_schema_head == SCHEMA_HEAD
    assert (
        build_parser().parse_args([*args, "--expected-schema-head", "20261104_0044"])
    ).expected_schema_head == "20261104_0044"
