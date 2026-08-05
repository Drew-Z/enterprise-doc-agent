from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.backup_database import (
    normalize_postgres_url,
    postgres_process_environment,
    run_backup,
)
from scripts.restore_database import (
    inspect_restore_preflight,
    restore_command,
    validate_restore_target,
)
from scripts.restore_database import main as restore_main
from scripts.rollback_release import execute_rollback, rollback_commands
from scripts.rollback_release import main as rollback_main


def test_database_url_normalization_removes_driver_suffix() -> None:
    assert (
        normalize_postgres_url("postgresql+psycopg://user:pass@db:5432/app")
        == "postgresql://user:pass@db:5432/app"
    )


def test_restore_command_is_single_transaction_and_targets_database_without_credentials() -> None:
    database_url = "postgresql+psycopg://user:super-secret@db:5432/app"
    command = restore_command(
        database_url=database_url,
        backup=Path("backup.dump"),
    )
    assert command[1:3] == ["--dbname", "app"]
    assert "--single-transaction" in command
    assert "--clean" not in command
    assert "--if-exists" not in command
    assert database_url not in command
    assert "super-secret" not in " ".join(command)


def test_database_credentials_are_transferred_through_libpq_environment() -> None:
    environment = postgres_process_environment(
        "postgresql+psycopg://user:super-secret@db:5432/app?sslmode=require"
    )
    assert environment["PGHOST"] == "db"
    assert environment["PGUSER"] == "user"
    assert environment["PGPASSWORD"] == "super-secret"
    assert environment["PGDATABASE"] == "app"
    assert environment["PGSSLMODE"] == "require"


def test_backup_process_keeps_database_credentials_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "backup.dump"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        captured.update(command=command, check=check, env=env)
        temporary_output = Path(command[command.index("--file") + 1])
        temporary_output.write_bytes(b"backup")

    monkeypatch.setattr("scripts.backup_database.shutil.which", lambda _: "pg_dump")
    monkeypatch.setattr("scripts.backup_database.subprocess.run", fake_run)

    record = run_backup(
        database_url="postgresql://user:super-secret@db:5432/app",
        output=output,
        overwrite=False,
        schemas=("public",),
    )

    command = captured["command"]
    environment = captured["env"]
    assert isinstance(command, list)
    assert isinstance(environment, dict)
    assert "super-secret" not in " ".join(command)
    assert environment["PGPASSWORD"] == "super-secret"
    assert command[command.index("--schema") + 1] == "public"
    assert "--strict-names" in command
    assert output.read_bytes() == b"backup"
    assert record["sha256"] == hashlib.sha256(b"backup").hexdigest()
    assert record["schemas"] == ["public"]
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_backup_rejects_schema_patterns_and_duplicates(tmp_path: Path) -> None:
    for schemas in (("public*",), ("public", "public")):
        with pytest.raises(ValueError):
            run_backup(
                database_url="postgresql://user:pass@db/app",
                output=tmp_path / f"backup-{len(schemas)}.dump",
                overwrite=False,
                schemas=schemas,
            )


def test_restore_preflight_rejects_unsafe_connected_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"archive")
    identity: dict[str, object] = {
        "current_database": "enterprise_doc_restore_drill",
        "current_user": "restore_operator",
        "server_addr": "10.0.0.17",
        "server_port": 5432,
        "server_version_num": 170006,
        "user_relation_count": 0,
        "other_connection_count": 0,
    }
    monkeypatch.setattr("scripts.restore_database.inspect_archive", lambda _: 42)
    monkeypatch.setattr("scripts.restore_database.postgres_client_major", lambda _: 17)
    monkeypatch.setattr(
        "scripts.restore_database.inspect_database_identity",
        lambda _: identity.copy(),
    )

    preflight = inspect_restore_preflight(
        database_url=("postgresql://user:pass@staging-db:5432/enterprise_doc_restore_drill"),
        backup=backup,
        expected_database="enterprise_doc_restore_drill",
        expected_server_address="10.0.0.17",
        require_empty=True,
    )
    assert preflight["archive_entry_count"] == 42
    assert preflight["pg_restore_client_major"] == 17

    identity["user_relation_count"] = 1
    with pytest.raises(ValueError, match="must not contain user relations"):
        inspect_restore_preflight(
            database_url="postgresql://user:pass@staging-db/enterprise_doc_restore_drill",
            backup=backup,
            expected_database="enterprise_doc_restore_drill",
            expected_server_address="10.0.0.17",
            require_empty=True,
        )

    identity["user_relation_count"] = 0
    identity["other_connection_count"] = 1
    with pytest.raises(ValueError, match="other active connections"):
        inspect_restore_preflight(
            database_url="postgresql://user:pass@staging-db/enterprise_doc_restore_drill",
            backup=backup,
            expected_database="enterprise_doc_restore_drill",
            expected_server_address="10.0.0.17",
            require_empty=True,
        )

    identity["other_connection_count"] = 0
    monkeypatch.setattr("scripts.restore_database.postgres_client_major", lambda _: 16)
    with pytest.raises(RuntimeError, match="older than the PostgreSQL server"):
        inspect_restore_preflight(
            database_url="postgresql://user:pass@staging-db/enterprise_doc_restore_drill",
            backup=backup,
            expected_database="enterprise_doc_restore_drill",
            expected_server_address="10.0.0.17",
            require_empty=True,
        )


def test_rollback_commands_are_explicit_and_ordered() -> None:
    commands = rollback_commands(
        namespace="enterprise-doc-agent-staging",
        revisions={"enterprise-doc-api": 42, "enterprise-doc-worker": 7},
    )
    assert commands == [
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-api",
            "--to-revision=42",
            "--dry-run=server",
            "--output=name",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-worker",
            "--to-revision=7",
            "--dry-run=server",
            "--output=name",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-api",
            "--to-revision=42",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-worker",
            "--to-revision=7",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "status",
            "deployment/enterprise-doc-api",
            "--timeout=300s",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "status",
            "deployment/enterprise-doc-worker",
            "--timeout=300s",
        ],
    ]


def test_rollback_rejects_ambiguous_or_unsafe_targets() -> None:
    with pytest.raises(ValueError):
        rollback_commands(
            namespace="enterprise-doc-agent-staging",
            revisions={"unknown": 1},
        )
    with pytest.raises(ValueError):
        rollback_commands(
            namespace="enterprise-doc-agent",
            revisions={"enterprise-doc-api": 1},
        )


def test_rollback_execution_records_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = rollback_commands(
        namespace="enterprise-doc-agent-staging",
        revisions={"enterprise-doc-api": 42},
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        calls.append(command)
        if command[4] == "status":
            raise subprocess.CalledProcessError(17, command)

    monkeypatch.setattr("scripts.rollback_release.subprocess.run", fake_run)
    result = execute_rollback(commands)

    assert result["status"] == "failed"
    assert result["completed_commands"] == commands[:2]
    assert result["failed_command"] == commands[2]
    assert result["error"] == "command exited with status 17"
    assert calls == commands


def test_rollback_cli_writes_structured_failure_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record_path = tmp_path / "rollback.json"

    def fail_first_command(command: list[str], *, check: bool) -> None:
        raise subprocess.CalledProcessError(23, command)

    monkeypatch.delenv("ROLLBACK_REVISIONS_JSON", raising=False)
    monkeypatch.setattr("scripts.rollback_release.shutil.which", lambda _: "kubectl")
    monkeypatch.setattr("scripts.rollback_release.subprocess.run", fail_first_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rollback_release.py",
            "--revision",
            "enterprise-doc-api=42",
            "--reason",
            "test rollback",
            "--migration-revision",
            "migration-1",
            "--confirm",
            "--record-path",
            str(record_path),
        ],
    )

    with pytest.raises(SystemExit, match="rollout rollback failed"):
        rollback_main()

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["completed_commands"] == []
    assert record["failed_command"][-2:] == ["--dry-run=server", "--output=name"]
    assert record["error"] == "command exited with status 23"


def test_restore_target_requires_an_isolated_named_staging_database() -> None:
    assert (
        validate_restore_target(
            database_url=(
                "postgresql+psycopg://user:pass@staging-db:5432/enterprise_doc_restore_drill"
            ),
            expected_host="staging-db",
            expected_database="enterprise_doc_restore_drill",
            source_database="enterprise_doc",
            environment="staging",
            production_confirmation=None,
        )
        == "postgresql://user:pass@staging-db:5432/enterprise_doc_restore_drill"
    )

    with pytest.raises(ValueError, match="does not match --expected-database"):
        validate_restore_target(
            database_url="postgresql://user:pass@staging-db:5432/enterprise_doc",
            expected_host="staging-db",
            expected_database="enterprise_doc_restore_drill",
            source_database="enterprise_doc",
            environment="staging",
            production_confirmation=None,
        )

    with pytest.raises(ValueError, match="must differ from source"):
        validate_restore_target(
            database_url="postgresql://user:pass@staging-db:5432/enterprise_doc",
            expected_host="staging-db",
            expected_database="enterprise_doc",
            source_database="enterprise_doc",
            environment="staging",
            production_confirmation=None,
        )

    with pytest.raises(ValueError, match="must start with enterprise_doc_restore_"):
        validate_restore_target(
            database_url="postgresql://user:pass@staging-db:5432/unrelated",
            expected_host="staging-db",
            expected_database="unrelated",
            source_database="enterprise_doc",
            environment="staging",
            production_confirmation=None,
        )


def test_restore_target_requires_matching_host_and_production_confirmation() -> None:
    with pytest.raises(ValueError, match="does not match --expected-host"):
        validate_restore_target(
            database_url="postgresql://user:pass@other-db:5432/app",
            expected_host="staging-db",
            expected_database="app",
            source_database=None,
            environment="test",
            production_confirmation=None,
        )

    with pytest.raises(ValueError, match="requires --confirm-production"):
        validate_restore_target(
            database_url="postgresql://user:pass@prod-db:5432/app",
            expected_host="prod-db",
            expected_database="app",
            source_database=None,
            environment="production",
            production_confirmation=None,
        )


def test_restore_cli_rejects_staging_source_database_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-executed")
    monkeypatch.setenv(
        "DATABASE__URL",
        "postgresql://user:pass@staging-db:5432/enterprise_doc",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restore_database.py",
            "--input",
            str(backup),
            "--environment",
            "staging",
            "--expected-host",
            "staging-db",
            "--expected-database",
            "enterprise_doc",
            "--source-database",
            "enterprise_doc",
            "--confirm",
        ],
    )

    with pytest.raises(SystemExit, match="must differ from source"):
        restore_main()


def test_confirmed_restore_records_the_isolated_database_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"isolated-restore-fixture")
    backup_sha256 = hashlib.sha256(backup.read_bytes()).hexdigest()
    record_path = tmp_path / "restore.json"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        captured.update(command=command, check=check, env=env)

    monkeypatch.setenv(
        "DATABASE__URL",
        ("postgresql://user:super-secret@staging-db:5432/enterprise_doc_restore_drill"),
    )
    monkeypatch.setattr("scripts.restore_database.shutil.which", lambda executable: executable)
    monkeypatch.setattr(
        "scripts.restore_database.inspect_restore_preflight",
        lambda **_: {
            "archive_entry_count": 42,
            "pg_restore_client_major": 17,
            "current_database": "enterprise_doc_restore_drill",
            "current_user": "restore_operator",
            "server_addr": "10.0.0.17",
            "server_port": 5432,
            "server_version_num": 170006,
            "user_relation_count": 0,
            "other_connection_count": 0,
        },
    )
    monkeypatch.setattr(
        "scripts.restore_database.inspect_database_identity",
        lambda _: {
            "current_database": "enterprise_doc_restore_drill",
            "server_addr": "10.0.0.17",
            "user_relation_count": 23,
        },
    )
    monkeypatch.setattr("scripts.restore_database.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restore_database.py",
            "--input",
            str(backup),
            "--environment",
            "staging",
            "--expected-host",
            "staging-db",
            "--expected-database",
            "enterprise_doc_restore_drill",
            "--expected-server-address",
            "10.0.0.17",
            "--source-database",
            "enterprise_doc",
            "--expected-sha256",
            backup_sha256,
            "--confirm",
            "--record-path",
            str(record_path),
        ],
    )

    restore_main()

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["source_database"] == "enterprise_doc"
    assert record["target_database"] == "enterprise_doc_restore_drill"
    assert record["target_server_address"] == "10.0.0.17"
    assert record["target_user_relation_count_before"] == 0
    assert record["target_user_relation_count_after"] == 23
    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:3] == ["--dbname", "enterprise_doc_restore_drill"]
    assert "super-secret" not in " ".join(command)
