from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from scripts import manage_tenant_admission as cli
from sqlalchemy.engine import make_url

from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.config import FoundationSettings
from tests.admission.conftest import AdmissionDatabase
from tests.admission.test_admission_cli import issue_args
from tests.admission.test_admission_lifecycle_integration import OPERATOR, service_for

pytestmark = pytest.mark.integration


def isolated_cli_settings(database: AdmissionDatabase) -> FoundationSettings:
    settings = FoundationSettings()
    url = make_url(settings.database.url.get_secret_value()).update_query_dict(
        {"options": f"-csearch_path={database.schema},public"}
    )
    return settings.model_copy(
        update={
            "database": settings.database.model_copy(
                update={"url": SecretStr(url.render_as_string(hide_password=False))}
            )
        }
    )


async def test_cli_issue_show_revoke_uses_database_without_returning_secret(
    admission_db: AdmissionDatabase,
    tmp_path: Path,
) -> None:
    settings = isolated_cli_settings(admission_db)
    path = tmp_path / "issued.json"
    try:
        args = cli.build_parser().parse_args([*issue_args(path), "--execute"])
        status, result = await cli.run_command(args, settings)
        assert status == 0 and result["status"] == "confirmed"
        prepared = json.loads(path.read_text(encoding="utf-8"))
        assert prepared["token"] not in str(result)
        for command in ("show", "revoke"):
            parameters = [
                command,
                "--grant-id",
                prepared["grantId"],
                "--operator",
                "operator",
                "--reason",
                "Inspect local grant",
            ]
            if command == "revoke":
                parameters.append("--execute")
            status, result = await cli.run_command(
                cli.build_parser().parse_args(parameters), settings
            )
            assert status == 0 and prepared["token"] not in str(result)
        assert result["grant"]["state"] == "revoked"
        assert path.exists()
    finally:
        path.unlink(missing_ok=True)


async def test_lost_issue_result_can_be_resolved_by_show(
    admission_db: AdmissionDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    settings = isolated_cli_settings(admission_db)
    path = tmp_path / "result-lost.json"
    original_issue = TenantAdmissionService.issue

    async def committed_then_disconnected(self: TenantAdmissionService, **kwargs: object) -> None:
        await original_issue(self, **kwargs)
        raise RuntimeError("synthetic lost acknowledgement")

    monkeypatch.setattr(TenantAdmissionService, "issue", committed_then_disconnected)
    try:
        args = cli.build_parser().parse_args([*issue_args(path), "--execute"])
        status, result = await cli.run_command(args, settings)
        assert status != 0 and result["status"] == "not_confirmed"
        prepared = json.loads(path.read_text(encoding="utf-8"))
        assert prepared["token"] not in str(result)
        snapshot = await service_for(admission_db).show(
            operator=OPERATOR, grant_id=UUID(prepared["grantId"])
        )
        assert snapshot.state == "pending" and str(snapshot.grant_id) == result["grantId"]
    finally:
        path.unlink(missing_ok=True)
