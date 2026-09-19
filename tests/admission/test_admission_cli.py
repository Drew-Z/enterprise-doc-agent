from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts import manage_tenant_admission as cli

from enterprise_doc_core.admission.contracts import prepare_admission_credential
from enterprise_doc_core.admission.credential_file import write_private_credential
from enterprise_doc_core.config import AppEnvironment, FoundationSettings


def issue_args(path: Path) -> list[str]:
    return [
        "issue",
        "--operator",
        "test-operator",
        "--reason",
        "Local CLI verification",
        "--issuer",
        "https://identity.example.test",
        "--email",
        "owner@example.test",
        "--expires-at",
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "--quota-bytes",
        "4096",
        "--seat-limit",
        "3",
        "--credential-file",
        str(path),
    ]


def test_preview_does_not_open_database_or_create_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("preview performed a write-side operation")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    monkeypatch.setattr(cli, "prepare_admission_credential", forbidden)
    path = tmp_path / "credential.json"
    assert cli.main(issue_args(path)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview" and result["operation"] == "issue"
    assert result["request"]["seat_limit"] == 3
    assert not path.exists()


def test_revoke_defaults_to_preview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("preview connected to database")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    assert (
        cli.main(
            [
                "revoke",
                "--grant-id",
                str(prepare_admission_credential().grant_id),
                "--operator",
                "operator",
                "--reason",
                "Preview revoke",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "preview"


@pytest.mark.parametrize("environment", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION])
def test_nonlocal_environment_is_rejected_before_creating_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: AppEnvironment,
) -> None:
    settings = FoundationSettings().model_copy(update={"app_env": environment})
    monkeypatch.setattr(cli, "FoundationSettings", lambda: settings)
    path = tmp_path / "forbidden.json"
    assert cli.main([*issue_args(path), "--execute"]) != 0
    assert json.loads(capsys.readouterr().out)["code"] == "admission_forbidden"
    assert not path.exists()


def test_private_file_is_exclusive_and_restricts_access(tmp_path: Path) -> None:
    path = tmp_path / "credential.json"
    credential = prepare_admission_credential()
    try:
        write_private_credential(path, credential)
        original = path.read_bytes()
        data = json.loads(original)
        assert data["grantId"] == str(credential.grant_id)
        assert data["token"] == credential.token.get_secret_value()
        with pytest.raises(OSError):
            write_private_credential(path, prepare_admission_credential())
        assert path.read_bytes() == original
        if os.name == "nt":
            command = (
                "$acl = Get-Acl -LiteralPath $env:ADMISSION_TEST_PATH; "
                "[pscustomobject]@{ protected = $acl.AreAccessRulesProtected; "
                "rules = @($acl.Access | ForEach-Object { [pscustomobject]@{ "
                "sid = $_.IdentityReference.Translate("
                "[System.Security.Principal.SecurityIdentifier]).Value; "
                "inherited = $_.IsInherited; type = $_.AccessControlType.ToString() } }) } "
                "| ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    r"C:\Users\zhang\AppData\Local\Microsoft\WindowsApps\pwsh.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                env={**os.environ, "ADMISSION_TEST_PATH": str(path)},
                check=True,
                capture_output=True,
                text=True,
            )
            acl = json.loads(result.stdout)
            assert acl["protected"]
            assert acl["rules"] == [{"sid": "S-1-3-4", "inherited": False, "type": "Allow"}]
        else:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        path.unlink(missing_ok=True)


def test_existing_file_failure_never_attempts_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("database opened after file creation failed")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    path = tmp_path / "existing.json"
    path.write_text("preserve this file", encoding="utf-8")
    try:
        assert cli.main([*issue_args(path), "--execute"]) != 0
        assert path.read_text(encoding="utf-8") == "preserve this file"
        assert json.loads(capsys.readouterr().out)["databaseWriteAttempted"] is False
    finally:
        path.unlink()


def test_unconfirmed_database_result_preserves_file_without_echoing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "uncertain.json"

    def fail_connection(*args: object, **kwargs: object) -> None:
        token = json.loads(path.read_text(encoding="utf-8"))["token"]
        raise RuntimeError(f"simulated transport error with sensitive parameter {token}")

    monkeypatch.setattr(cli, "create_database_engine", fail_connection)
    try:
        assert cli.main([*issue_args(path), "--execute"]) != 0
        retained = json.loads(path.read_text(encoding="utf-8"))
        output = capsys.readouterr()
        assert retained["token"] not in output.out + output.err
        receipt = json.loads(output.out)
        assert receipt["status"] == "not_confirmed" and receipt["grantId"] == retained["grantId"]
    finally:
        path.unlink(missing_ok=True)


def test_unknown_cli_arguments_are_not_echoed(capsys: pytest.CaptureFixture[str]) -> None:
    credential = prepare_admission_credential()
    assert cli.main(["accept", "--token", credential.token.get_secret_value()]) == 2
    output = capsys.readouterr()
    assert credential.token.get_secret_value() not in output.out + output.err


def test_file_sync_failure_keeps_file_and_does_not_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sync-failed.json"

    def fail_sync(descriptor: int) -> None:
        raise OSError("synthetic sync failure")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("database opened before durable credential file")

    monkeypatch.setattr(os, "fsync", fail_sync)
    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    try:
        assert cli.main([*issue_args(path), "--execute"]) != 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "file_failed" and result["databaseWriteAttempted"] is False
        assert path.exists()
    finally:
        path.unlink(missing_ok=True)
