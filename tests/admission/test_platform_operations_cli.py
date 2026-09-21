from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest


def invoke_operator(
    arguments: list[str],
    *,
    environment: dict[str, str | None] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("DATABASE", "BROWSER_AUTH", "APP_ENV", "PGHOST", "PGPORT", "PGSERVICE")
        )
    }
    process_env.update(
        APP_ENV="staging",
        DATABASE__URL="postgresql+psycopg://operator:synthetic-password@database.invalid/docagent",
        BROWSER_AUTH__ENABLED="true",
        BROWSER_AUTH__ISSUER="https://identity.example.test/realms/docagent",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    for key, value in (environment or {}).items():
        if value is None:
            process_env.pop(key, None)
        else:
            process_env[key] = value
    return subprocess.run(
        [sys.executable, "-B", "-m", "enterprise_doc_core.operations", *arguments],
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
        cwd=cwd,
    )


def target_arguments() -> list[str]:
    return [
        "--environment",
        "staging",
        "--database-host",
        "database.invalid",
        "--database-name",
        "docagent",
        "--operator",
        "pilot-admin",
        "--reason",
        "Approved pilot onboarding",
    ]


def issue_arguments(credential_file: Path) -> list[str]:
    return [
        *target_arguments(),
        "admission",
        "issue",
        "--email",
        "owner@example.test",
        "--expires-at",
        (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        "--quota-bytes",
        "104857600",
        "--seat-limit",
        "2",
        "--credential-file",
        str(credential_file),
    ]


def test_formal_operator_previews_without_database_or_credential_write(tmp_path: Path) -> None:
    credential_file = tmp_path / "admission.json"
    result = invoke_operator(issue_arguments(credential_file))
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "preview"
    assert output["databaseValidated"] is False
    assert output["target"] == {
        "environment": "staging",
        "host": "database.invalid",
        "port": 5432,
        "database": "docagent",
    }
    assert output["request"]["issuer"] == "https://identity.example.test/realms/docagent"
    assert not credential_file.exists()
    assert "synthetic-password" not in result.stdout + result.stderr
    assert "adm1_" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("environment", "code"),
    [
        ({"APP_ENV": None}, "operations_invalid_configuration"),
        ({"APP_ENV": "local"}, "operations_environment_forbidden"),
        ({"APP_ENV": "test"}, "operations_environment_forbidden"),
        ({"APP_ENV": "production"}, "operations_target_mismatch"),
        ({"DATABASE__URL": None}, "operations_invalid_configuration"),
        ({"DATABASE__URL": None, "DATABASE__POOL_SIZE": "1"}, "operations_invalid_configuration"),
        ({"BROWSER_AUTH__ENABLED": "false"}, "operations_browser_identity_unavailable"),
        ({"BROWSER_AUTH__ISSUER": None}, "operations_browser_identity_unavailable"),
        (
            {"BROWSER_AUTH__ISSUER": "http://identity.example.test"},
            "operations_browser_identity_unavailable",
        ),
        (
            {"BROWSER_AUTH__ISSUER": "https://user:secret@identity.example.test"},
            "operations_browser_identity_unavailable",
        ),
        (
            {"BROWSER_AUTH__ISSUER": "https://identity.example.test?other=issuer"},
            "operations_browser_identity_unavailable",
        ),
        ({"PGHOSTADDR": "127.0.0.2"}, "operations_database_override_forbidden"),
        ({"PGPORT": "5433"}, "operations_database_override_forbidden"),
        ({"PGSERVICE": "another-database"}, "operations_database_override_forbidden"),
    ],
    ids=[
        "environment-required",
        "local-refused",
        "test-refused",
        "environment-mismatch",
        "database-required",
        "no-inherited-url-default",
        "login-disabled",
        "issuer-required",
        "https-required",
        "no-userinfo",
        "no-query",
        "no-pghostaddr",
        "no-pgport",
        "no-pgservice",
    ],
)
def test_invalid_runtime_configuration_stops_before_writes(
    tmp_path: Path, environment: dict[str, str | None], code: str
) -> None:
    path = tmp_path / "not-written.json"
    result = invoke_operator([*issue_arguments(path), "--execute"], environment=environment)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"status": "failed", "code": code}
    assert not path.exists()
    assert result.stderr == ""


@pytest.mark.parametrize("key", ["host", "hostaddr", "port", "dbname", "service", "servicefile"])
def test_database_query_cannot_redirect_the_reviewed_target(tmp_path: Path, key: str) -> None:
    path = tmp_path / "not-written.json"
    result = invoke_operator(
        [*issue_arguments(path), "--execute"],
        environment={
            "DATABASE__URL": f"postgresql+psycopg://operator:secret@database.invalid/docagent?{key}=other",
        },
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "operations_database_override_forbidden"
    assert not path.exists()


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--database-host", "other.invalid"),
        ("--database-port", "5433"),
        ("--database-name", "other-database"),
    ],
)
def test_mismatched_target_never_creates_a_credential(
    tmp_path: Path, flag: str, value: str
) -> None:
    path = tmp_path / "not-written.json"
    arguments = issue_arguments(path)
    if flag in arguments:
        arguments[arguments.index(flag) + 1] = value
    else:
        arguments[0:0] = [flag, value]
    result = invoke_operator([*arguments, "--execute"])
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "operations_target_mismatch"
    assert not path.exists()


def test_production_preview_uses_explicit_production_target(tmp_path: Path) -> None:
    arguments = issue_arguments(tmp_path / "not-written.json")
    arguments[arguments.index("--environment") + 1] = "production"
    result = invoke_operator(arguments, environment={"APP_ENV": "production"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["target"]["environment"] == "production"


def test_working_directory_env_file_does_not_select_a_database(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=staging\nDATABASE__URL=postgresql+psycopg://operator:secret@database.invalid/docagent\n",
        encoding="utf-8",
    )
    try:
        result = invoke_operator(
            issue_arguments(tmp_path / "not-written.json"),
            environment={"APP_ENV": None, "DATABASE__URL": None},
            cwd=tmp_path,
        )
        assert result.returncode == 2
        assert json.loads(result.stdout)["code"] == "operations_invalid_configuration"
    finally:
        env_file.unlink()


def test_unknown_arguments_do_not_echo_accidental_secrets(tmp_path: Path) -> None:
    accidental = "adm1_" + "x" * 43
    result = invoke_operator(
        [*issue_arguments(tmp_path / "not-written.json"), "--token", accidental]
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "operations_invalid_arguments"
    assert accidental not in result.stdout + result.stderr


def test_existing_credential_is_preserved_without_database_attempt(tmp_path: Path) -> None:
    path = tmp_path / "existing.json"
    original = b"existing private handoff"
    path.write_bytes(original)
    result = invoke_operator([*issue_arguments(path), "--execute"])
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["status"] == "file_failed"
    assert output["databaseWriteAttempted"] is False
    assert path.read_bytes() == original


def test_connection_failure_retains_prepared_file_for_recovery(tmp_path: Path) -> None:
    path = tmp_path / "retained.json"
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        arguments = issue_arguments(path)
        arguments[arguments.index("--database-host") + 1] = "127.0.0.1"
        arguments[0:0] = ["--database-port", str(port)]
        result = invoke_operator(
            [*arguments, "--execute"],
            environment={
                "DATABASE__URL": f"postgresql+psycopg://operator:synthetic-password@127.0.0.1:{port}/docagent",
            },
        )
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["status"] == "not_confirmed"
    prepared = json.loads(path.read_text(encoding="utf-8"))
    assert output["grantId"] == prepared["grantId"]
    assert prepared["state"] == "prepared"
    assert prepared["token"] not in result.stdout + result.stderr
    assert "synthetic-password" not in result.stdout + result.stderr
    path.unlink()


def test_entitlement_preview_preserves_explicit_zero_limit() -> None:
    now = datetime.now(UTC)
    result = invoke_operator(
        [
            *target_arguments(),
            "entitlement",
            "configure",
            "--tenant-id",
            str(uuid4()),
            "--entitlement-id",
            str(uuid4()),
            "--expected-version",
            "0",
            "--plan-code",
            "pilot",
            "--period-start",
            now.isoformat(),
            "--period-end",
            (now + timedelta(days=7)).isoformat(),
            "--request-limit",
            "0",
        ],
        environment={"BROWSER_AUTH__ENABLED": "false"},
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["status"] == "preview" and output["databaseValidated"] is False
    assert output["request"]["provider_request_limit"] == 0


def test_entitlement_list_bound_is_checked_before_connection() -> None:
    result = invoke_operator(
        [
            *target_arguments(),
            "entitlement",
            "list",
            "--tenant-id",
            str(uuid4()),
            "--limit",
            "101",
        ]
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "entitlement_invalid_limit"
