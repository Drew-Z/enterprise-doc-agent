from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.run_analysis_rollback_compatibility import (
    ANALYSIS_TABLE,
    SCHEMA_HEAD,
    CompatibilityConfig,
    CompatibilityError,
    build_runtime_environment,
    dependency_args,
    http_probe,
    migration_args,
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


def test_wait_probe_retries_until_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(({"status": "unreachable"}, {"status": "passed", "http_status": 200}))
    monkeypatch.setattr("scripts.run_analysis_rollback_compatibility.time.sleep", lambda _: None)
    observed = wait_probe(lambda: next(values), timeout_seconds=1, label="test")
    assert observed == {"status": "passed", "http_status": 200}


def test_http_probe_reports_openapi_contract_without_returning_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, _: int) -> bytes:
            return json.dumps(
                {"openapi": "3.1.0", "paths": {"/api/analysis/projects": {}}}
            ).encode()

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Response(),
    )
    result = http_probe("http://127.0.0.1/openapi.json", timeout_seconds=1)
    assert result == {
        "status": "passed",
        "http_status": 200,
        "openapi_document": True,
        "analysis_route_present": True,
    }
    assert "body" not in result


def test_schema_contract_constants_are_the_current_analysis_gate() -> None:
    assert SCHEMA_HEAD == "20261012_0043"
    assert ANALYSIS_TABLE == "analysis_projects"
