from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr
from scripts import manage_tenant_entitlements as cli
from sqlalchemy import select
from tests.billing.test_entitlement_cli import configuration_args

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.config import AppEnvironment, FoundationSettings

pytestmark = pytest.mark.integration


async def test_lost_commit_confirmation_is_resolved_by_cli_show_and_replay(
    billing_database, monkeypatch
) -> None:
    factory, (tenant_id, other_id) = billing_database
    settings = FoundationSettings(_env_file=None)
    settings = settings.model_copy(
        update={
            "app_env": AppEnvironment.TEST,
            "database": settings.database.model_copy(
                update={
                    "url": SecretStr(factory.kw["bind"].url.render_as_string(hide_password=False))
                }
            ),
        }
    )
    entitlement_id = uuid4()
    args = cli.build_parser().parse_args(
        [*configuration_args(tenant_id, entitlement_id), "--execute"]
    )
    original = EntitlementAdministrationService.configure

    async def lost_acknowledgement(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("synthetic lost acknowledgement after real commit")

    monkeypatch.setattr(EntitlementAdministrationService, "configure", lost_acknowledgement)
    code, result = await cli.run_command(args, settings)
    assert code == 1 and result["status"] == "not_confirmed"
    assert result["entitlementId"] == str(entitlement_id)
    monkeypatch.setattr(EntitlementAdministrationService, "configure", original)
    for target, expected in ((tenant_id, 0), (other_id, 1)):
        show = cli.build_parser().parse_args(
            [
                "show",
                "--tenant-id",
                str(target),
                "--entitlement-id",
                str(entitlement_id),
                "--operator",
                "test-operator",
                "--reason",
                "Resolve operation",
            ]
        )
        code, result = await cli.run_command(show, settings)
        assert code == expected
        if expected == 0:
            assert result["status"] == "confirmed" and result["entitlement"]["version"] == 1
        else:
            assert result["code"] == "entitlement_not_found"
    code, replay = await cli.run_command(args, settings)
    assert code == 0 and replay["replayed"] is True
    listing = cli.build_parser().parse_args(
        [
            "list",
            "--tenant-id",
            str(tenant_id),
            "--operator",
            "test-operator",
            "--reason",
            "Check history",
        ]
    )
    code, result = await cli.run_command(listing, settings)
    assert code == 0 and result["latestVersion"] == 1 and len(result["entitlements"]) == 1
    async with factory() as session:
        audits = (
            await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        ).all()
    assert len(audits) == 1
