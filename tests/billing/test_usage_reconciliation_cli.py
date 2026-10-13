import json
import socket
from uuid import uuid4

import pytest
from tests.admission.test_platform_operations_cli import invoke_operator, target_arguments


def export_arguments():
    return [
        *target_arguments(),
        "usage",
        "export",
        "--tenant-id",
        str(uuid4()),
        "--start",
        "2026-09-01T00:00:00Z",
        "--end",
        "2026-09-02T00:00:00Z",
    ]


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--start", "2026-09-01T00:00:00"),
        ("--end", "2026-11-01T00:00:00Z"),
        ("--limit-per-source", "5001"),
    ],
)
def test_invalid_export_window_is_rejected_before_connection(flag, value):
    args = export_arguments()
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args.extend([flag, value])
    result = invoke_operator(args)
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "reconciliation_invalid_window"
    assert result.stderr == ""


def test_export_requires_exact_target_and_never_echoes_unknown_secrets():
    result = invoke_operator(export_arguments(), environment={"APP_ENV": "production"})
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "operations_target_mismatch"
    result = invoke_operator([*export_arguments(), "--password", "private-test-value"])
    assert result.returncode == 2
    assert "private-test-value" not in result.stdout + result.stderr


def test_export_connection_error_is_not_an_empty_or_partial_report():
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        args = export_arguments()
        args[args.index("--database-host") + 1] = "127.0.0.1"
        args[0:0] = ["--database-port", str(port)]
        result = invoke_operator(
            args,
            environment={
                "DATABASE__URL": f"postgresql+psycopg://operator:synthetic-password@127.0.0.1:{port}/docagent"
            },
        )
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["status"] == "failed" and output["code"] == "reconciliation_store_unavailable"
    assert "export" not in output
    assert "synthetic-password" not in result.stdout + result.stderr
