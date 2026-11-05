from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from scripts.local_recovery_drill import (
    LocalRecoveryDrillError,
    compose_command,
    main,
    run_drill,
    validate_database_names,
)


def test_recovery_drill_requires_isolated_prefixed_database() -> None:
    validate_database_names("enterprise_doc", "enterprise_doc_restore_drill")
    with pytest.raises(LocalRecoveryDrillError, match="differ"):
        validate_database_names("enterprise_doc", "enterprise_doc")
    with pytest.raises(LocalRecoveryDrillError, match="start with"):
        validate_database_names("enterprise_doc", "other_restore")
    with pytest.raises(LocalRecoveryDrillError, match="invalid"):
        validate_database_names("enterprise-doc", "enterprise_doc_restore_drill")


def test_compose_command_is_explicit_and_non_shell() -> None:
    assert compose_command(Path("infra/compose/docker-compose.yml"), "ps", "postgres") == [
        "docker",
        "compose",
        "-f",
        "infra/compose/docker-compose.yml",
        "ps",
        "postgres",
    ]


@dataclass
class ComposeBoundary:
    target_exists: bool = False
    restore_error: str | None = None
    create_race: bool = False
    commands: list[list[str]] = field(default_factory=list)

    def run(self, command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        output = ""
        if "psql" in command:
            sql = command[-1]
            if "pg_database" in sql:
                output = "1" if self.target_exists else ""
            elif "information_schema.tables" in sql:
                output = "alembic_version\nprobe"
            elif "count(*)" in sql:
                output = "1"
            elif "version_num" in sql:
                output = "20260924_0030"
            else:
                raise AssertionError(sql)
        elif "pg_dump" in command:
            options["stdout"].write(b"synthetic-backup")
        elif "createdb" in command:
            if self.create_race:
                self.target_exists = True
            if self.target_exists:
                raise subprocess.CalledProcessError(1, command)
            self.target_exists = True
        elif "dropdb" in command:
            self.target_exists = False
        elif "pg_restore" in command:
            assert options["stdin"].read() == b"synthetic-backup"
            if self.restore_error == "timeout":
                raise subprocess.TimeoutExpired(command, 300)
            if self.restore_error == "failed":
                raise subprocess.CalledProcessError(1, command)
        elif command[0] == "git":
            output = "a" * 40
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, output, "")


def drill(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "root": tmp_path / "repository",
        "compose_file": tmp_path / "repository/infra/compose/docker-compose.yml",
        "output_dir": tmp_path / "private/drill",
        "source_database": "enterprise_doc",
        "restore_database": "enterprise_doc_restore_unique",
        "postgres_user": "enterprise_doc",
        "keep_restore_database": False,
    }
    return run_drill(**{**arguments, **overrides})


def test_existing_restore_database_is_never_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = ComposeBoundary(target_exists=True)
    monkeypatch.setattr(subprocess, "run", boundary.run)

    with pytest.raises(LocalRecoveryDrillError, match="already exists"):
        drill(tmp_path)

    assert boundary.target_exists
    assert not (tmp_path / "private/drill").exists()
    assert all(
        not {"createdb", "dropdb", "pg_dump", "pg_restore"}.intersection(command)
        for command in boundary.commands
    )


@pytest.mark.parametrize("location", ["repository/private", "private/drill"])
def test_invalid_output_location_is_rejected_before_database_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    boundary = ComposeBoundary()
    monkeypatch.setattr(subprocess, "run", boundary.run)
    output_dir = tmp_path / location
    if location == "private/drill":
        output_dir.mkdir(parents=True)
        (output_dir / "database.dump").write_bytes(b"previous-backup")

    with pytest.raises(LocalRecoveryDrillError):
        drill(tmp_path, output_dir=output_dir)

    assert boundary.commands == []
    if location == "private/drill":
        assert (output_dir / "database.dump").read_bytes() == b"previous-backup"


def test_private_restore_outputs_preserve_hashes_and_external_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = ComposeBoundary()
    monkeypatch.setattr(subprocess, "run", boundary.run)

    report = drill(tmp_path)

    assert report["status"] == "blocked_external"
    assert report["artifact_scope"] == "local-private"
    assert report["measurements"]["local_data_inventory_match"] is True
    assert not boundary.target_exists
    for artifact in report["artifacts"]:
        path = Path(artifact["path"])
        assert path.is_absolute()
        assert path.is_relative_to(tmp_path / "private/drill")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert not (tmp_path / "repository").exists()


@pytest.mark.parametrize("restore_error", ["failed", "timeout"])
@pytest.mark.parametrize("keep", [False, True])
def test_failed_restore_only_cleans_owned_database_unless_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_error: str, keep: bool
) -> None:
    boundary = ComposeBoundary(restore_error=restore_error)
    monkeypatch.setattr(subprocess, "run", boundary.run)

    with pytest.raises(subprocess.SubprocessError):
        drill(tmp_path, keep_restore_database=keep)

    assert boundary.target_exists is keep
    assert (tmp_path / "private/drill/database.dump").read_bytes() == b"synthetic-backup"
    drops = [command for command in boundary.commands if "dropdb" in command]
    assert len(drops) == (0 if keep else 1)
    assert all(command[-1] == "enterprise_doc_restore_unique" for command in drops)


def test_target_created_by_another_operator_is_not_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = ComposeBoundary(create_race=True)
    monkeypatch.setattr(subprocess, "run", boundary.run)

    with pytest.raises(subprocess.CalledProcessError):
        drill(tmp_path)

    assert boundary.target_exists
    assert all("dropdb" not in command for command in boundary.commands)


def test_cli_preview_does_not_connect_or_create_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    boundary = ComposeBoundary()
    monkeypatch.setattr(subprocess, "run", boundary.run)
    output = tmp_path / "private/drill"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "local_recovery_drill",
            "--root",
            str(tmp_path / "repository"),
            "--output-dir",
            str(output),
        ],
    )

    main()

    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert Path(preview["report_path"]) == output / "report.json"
    assert not output.exists() and not boundary.commands
