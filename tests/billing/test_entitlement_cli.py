from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from scripts import manage_tenant_entitlements as cli

from enterprise_doc_core.config import AppEnvironment, FoundationSettings


def configuration_args(tenant_id=None, entitlement_id=None) -> list[str]:
    start = datetime.now(UTC).replace(microsecond=0)
    return [
        "configure",
        "--tenant-id",
        str(tenant_id or uuid4()),
        "--entitlement-id",
        str(entitlement_id or uuid4()),
        "--expected-version",
        "0",
        "--plan-code",
        "trial",
        "--period-start",
        start.isoformat(),
        "--period-end",
        (start + timedelta(days=1)).isoformat(),
        "--request-limit",
        "2",
        "--operator",
        "test-operator",
        "--reason",
        "Local CLI test",
    ]


def test_configuration_defaults_to_preview_without_database_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preview must not open a database")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    assert cli.main(configuration_args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview"
    assert result["databaseValidated"] is False
    assert result["request"]["provider_request_limit"] == 2
    assert result["request"]["agent_task_limit"] == 0
    assert result["request"]["document_bytes_limit"] == 0


def test_product_quota_cli_preview_is_explicit_and_does_not_open_database(monkeypatch, capsys):
    monkeypatch.setattr(cli, "create_database_engine", lambda *_: pytest.fail("database opened"))
    assert (
        cli.main(
            [
                "configure-products",
                "--tenant-id",
                str(uuid4()),
                "--entitlement-id",
                str(uuid4()),
                "--expected-version",
                "1",
                "--agent-task-limit",
                "10",
                "--document-bytes-limit",
                "4096",
                "--operator",
                "test",
                "--reason",
                "enable products",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "preview" and output["databaseValidated"] is False
    assert output["request"]["agent_task_limit"] == 10
    assert output["request"]["document_bytes_limit"] == 4096


@pytest.mark.parametrize("environment", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION])
def test_cli_rejects_nonlocal_environments_before_connecting(
    monkeypatch, capsys, environment
) -> None:
    settings = FoundationSettings(_env_file=None).model_copy(update={"app_env": environment})
    monkeypatch.setattr(cli, "FoundationSettings", lambda: settings)
    monkeypatch.setattr(cli, "create_database_engine", lambda *_: pytest.fail("database opened"))
    assert cli.main([*configuration_args(), "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "entitlement_local_only"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://example@remote.example.test/db",
        "postgresql+psycopg://example@127.0.0.1/db?host=remote.example.test",
        "postgresql+psycopg://example@127.0.0.1/db?service=remote",
    ],
)
def test_cli_rejects_remote_database_and_host_overrides(monkeypatch, capsys, url) -> None:
    settings = FoundationSettings(_env_file=None)
    settings = settings.model_copy(
        update={"database": settings.database.model_copy(update={"url": SecretStr(url)})}
    )
    monkeypatch.setattr(cli, "FoundationSettings", lambda: settings)
    monkeypatch.setattr(cli, "create_database_engine", lambda *_: pytest.fail("database opened"))
    assert cli.main(configuration_args()) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "entitlement_local_only"


def test_cli_preserves_receipt_id_when_configuration_cannot_be_confirmed(
    monkeypatch, capsys
) -> None:
    entitlement_id = uuid4()
    sentinel = "do-not-print-synthetic-connection-detail"

    def disconnected(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(cli, "create_database_engine", disconnected)
    assert cli.main([*configuration_args(entitlement_id=entitlement_id), "--execute"]) == 1
    output = capsys.readouterr()
    assert sentinel not in output.out + output.err
    result = json.loads(output.out)
    assert result["status"] == "not_confirmed"
    assert result["entitlementId"] == str(entitlement_id)


def test_cli_does_not_echo_unknown_or_invalid_arguments(capsys) -> None:
    sentinel = "do-not-print-synthetic-secret"
    assert cli.main(["configure", "--token", sentinel]) == 2
    output = capsys.readouterr()
    assert sentinel not in output.out + output.err
    assert json.loads(output.out)["code"] == "entitlement_invalid_arguments"
