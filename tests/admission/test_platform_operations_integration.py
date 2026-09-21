from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import AsyncConnection, OperationalError
from pydantic import SecretStr
from sqlalchemy import Connection, select

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.admission.errors import AdmissionDenied
from enterprise_doc_core.admission.models import TenantAdmissionEvent
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing.service import EntitlementUsageService
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.operations import cli
from tests.admission.conftest import AdmissionDatabase
from tests.admission.test_platform_operations_cli import invoke_operator

pytestmark = pytest.mark.integration
ISSUER = "https://identity.example.test/realms/docagent"


def settings_for(database: AdmissionDatabase) -> cli.OperationsSettings:
    url = database.engine.url.update_query_dict(
        {"options": f"-csearch_path={database.schema},public"}
    )
    return cli.OperationsSettings(
        _env_file=None,
        app_env="staging",
        database={"url": url.render_as_string(hide_password=False)},
        browser_auth={"enabled": True, "issuer": ISSUER},
    )


def operator_arguments(database: AdmissionDatabase) -> list[str]:
    url = database.engine.url
    return [
        "--environment",
        "staging",
        "--database-host",
        str(url.host),
        "--database-port",
        str(url.port or 5432),
        "--database-name",
        str(url.database),
        "--operator",
        "pilot-admin",
        "--reason",
        "Approved synthetic pilot",
    ]


async def execute(
    database: AdmissionDatabase, arguments: list[str]
) -> tuple[int, dict[str, object]]:
    return await cli.run_command(
        cli.build_parser().parse_args([*operator_arguments(database), *arguments]),
        settings_for(database),
    )


async def issue(database: AdmissionDatabase, path: Path) -> tuple[int, dict[str, object]]:
    return await execute(
        database,
        [
            "admission",
            "issue",
            "--email",
            "owner@example.test",
            "--expires-at",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "--quota-bytes",
            "104857600",
            "--seat-limit",
            "2",
            "--credential-file",
            str(path),
            "--execute",
        ],
    )


async def test_formal_admission_is_accepted_and_operator_can_resolve_company(
    admission_db: AdmissionDatabase, tmp_path: Path
) -> None:
    credential_file = tmp_path / "operator-admission.json"
    try:
        process = await asyncio.to_thread(
            invoke_operator,
            [
                *operator_arguments(admission_db),
                "admission",
                "issue",
                "--email",
                "owner@example.test",
                "--expires-at",
                (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "--quota-bytes",
                "104857600",
                "--seat-limit",
                "2",
                "--credential-file",
                str(credential_file),
                "--execute",
            ],
            environment={
                "DATABASE__URL": settings_for(admission_db).database.url.get_secret_value(),
            },
        )
        assert process.returncode == 0
        issued = json.loads(process.stdout)
        assert issued["status"] == "confirmed"
        prepared = json.loads(credential_file.read_text(encoding="utf-8"))
        assert prepared["state"] == "prepared"
        assert prepared["token"] not in json.dumps(issued, default=str)
        service = TenantAdmissionService(
            session_factory=admission_db.sessions, trusted_issuers=frozenset({ISSUER})
        )
        receipt = await service.accept(
            token=SecretStr(prepared["token"]),
            identity=VerifiedAdmissionIdentity(
                issuer=ISSUER,
                subject=f"pilot-{uuid4()}",
                email="owner@example.test",
                email_verified=True,
            ),
            tenant_name="Approved pilot company",
        )
        status, shown = await execute(
            admission_db,
            [
                "admission",
                "show",
                "--grant-id",
                prepared["grantId"],
            ],
        )
        assert status == 0 and shown["status"] == "confirmed"
        snapshot = json.loads(json.dumps(shown, default=str))["grant"]
        assert snapshot["state"] == "accepted"
        assert UUID(snapshot["tenant_id"]) == receipt.tenant_id
        assert prepared["token"] not in json.dumps(shown, default=str)
    finally:
        credential_file.unlink(missing_ok=True)


async def test_committed_issue_with_lost_driver_acknowledgement_can_be_queried(
    admission_db: AdmissionDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_commit = AsyncConnection.commit
    lost = False

    async def commit_then_lose_ack(connection):
        nonlocal lost
        await original_commit(connection)
        if not lost:
            lost = True
            raise OperationalError("synthetic lost acknowledgement containing sensitive detail")

    monkeypatch.setattr(AsyncConnection, "commit", commit_then_lose_ack)
    path = tmp_path / "acknowledgement-lost.json"
    try:
        status, result = await issue(admission_db, path)
        assert status == 1 and result["status"] == "not_confirmed"
        prepared = json.loads(path.read_text(encoding="utf-8"))
        assert result["grantId"] == prepared["grantId"]
        assert "sensitive detail" not in json.dumps(result, default=str)
        status, shown = await execute(
            admission_db,
            [
                "admission",
                "show",
                "--grant-id",
                prepared["grantId"],
            ],
        )
        assert status == 0
        assert json.loads(json.dumps(shown, default=str))["grant"]["state"] == "pending"
        async with admission_db.sessions() as session:
            events = list(
                (
                    await session.scalars(
                        select(TenantAdmissionEvent).where(
                            TenantAdmissionEvent.grant_id == UUID(prepared["grantId"]),
                        )
                    )
                ).all()
            )
        assert len(events) == 1 and events[0].action == "issued"
        assert prepared["token"] not in json.dumps([result, shown], default=str)
    finally:
        path.unlink(missing_ok=True)


async def test_formal_revocation_requires_execute_and_preserves_operator_audit(
    admission_db: AdmissionDatabase, tmp_path: Path
) -> None:
    credential_file = tmp_path / "revoke-admission.json"
    try:
        status, issued = await issue(admission_db, credential_file)
        assert status == 0
        prepared = json.loads(credential_file.read_text(encoding="utf-8"))
        revoke = ["admission", "revoke", "--grant-id", prepared["grantId"]]
        status, preview = await execute(admission_db, revoke)
        assert status == 0 and preview["status"] == "preview"
        status, pending = await execute(
            admission_db,
            [
                "admission",
                "show",
                "--grant-id",
                prepared["grantId"],
            ],
        )
        assert status == 0
        assert json.loads(json.dumps(pending, default=str))["grant"]["state"] == "pending"
        status, revoked = await execute(admission_db, [*revoke, "--execute"])
        assert status == 0
        assert json.loads(json.dumps(revoked, default=str))["grant"]["state"] == "revoked"
        with pytest.raises(AdmissionDenied):
            await TenantAdmissionService(
                session_factory=admission_db.sessions, trusted_issuers=frozenset({ISSUER})
            ).accept(
                token=SecretStr(prepared["token"]),
                identity=VerifiedAdmissionIdentity(
                    issuer=ISSUER,
                    subject=f"pilot-{uuid4()}",
                    email="owner@example.test",
                    email_verified=True,
                ),
                tenant_name="Not opened",
            )
        async with admission_db.sessions() as session:
            events = list(
                (
                    await session.scalars(
                        select(TenantAdmissionEvent).where(
                            TenantAdmissionEvent.grant_id == UUID(prepared["grantId"]),
                        )
                    )
                ).all()
            )
            assert {event.action for event in events} == {"issued", "revoked"}
            assert all(event.operator_id == "pilot-admin" for event in events)
            assert all(event.reason == "Approved synthetic pilot" for event in events)
        assert prepared["token"] not in json.dumps([issued, pending, revoked], default=str)
    finally:
        credential_file.unlink(missing_ok=True)


async def add_usage_schema(database: AdmissionDatabase) -> None:
    def upgrade(connection: Connection) -> None:
        migration = import_module(
            "enterprise_doc_core.db.migrations.versions.20260914_0026_entitlements_usage"
        )
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            migration.upgrade()

    async with database.engine.begin() as connection:
        await connection.run_sync(upgrade)


async def open_company(database: AdmissionDatabase, credential_file: Path) -> UUID:
    status, issued = await issue(database, credential_file)
    assert status == 0 and issued["status"] == "confirmed"
    prepared = json.loads(await asyncio.to_thread(credential_file.read_text, encoding="utf-8"))
    receipt = await TenantAdmissionService(
        session_factory=database.sessions, trusted_issuers=frozenset({ISSUER})
    ).accept(
        token=SecretStr(prepared["token"]),
        identity=VerifiedAdmissionIdentity(
            issuer=ISSUER,
            subject=f"pilot-{uuid4()}",
            email="owner@example.test",
            email_verified=True,
        ),
        tenant_name="Approved pilot company",
    )
    return receipt.tenant_id


async def test_formal_entitlement_replay_keeps_consumed_usage_and_one_audit(
    admission_db: AdmissionDatabase, tmp_path: Path
) -> None:
    await add_usage_schema(admission_db)
    credential_file = tmp_path / "entitlement-admission.json"
    try:
        tenant_id = await open_company(admission_db, credential_file)
        entitlement_id = uuid4()
        now = datetime.now(UTC)
        configure = [
            "entitlement",
            "configure",
            "--tenant-id",
            str(tenant_id),
            "--entitlement-id",
            str(entitlement_id),
            "--expected-version",
            "0",
            "--plan-code",
            "pilot",
            "--period-start",
            (now - timedelta(hours=1)).isoformat(),
            "--period-end",
            (now + timedelta(days=7)).isoformat(),
            "--request-limit",
            "2",
            "--execute",
        ]
        status, configured = await execute(admission_db, configure)
        assert status == 0 and configured["status"] == "confirmed"
        assert configured["replayed"] is False

        usage = EntitlementUsageService(session_factory=admission_db.sessions)
        operation_id = uuid4()
        await usage.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
        await usage.settle_provider_request(tenant_id=tenant_id, operation_id=operation_id)
        status, replayed = await execute(admission_db, configure)
        assert status == 0 and replayed["replayed"] is True

        status, shown = await execute(
            admission_db,
            [
                "entitlement",
                "show",
                "--tenant-id",
                str(tenant_id),
                "--entitlement-id",
                str(entitlement_id),
            ],
        )
        assert status == 0
        snapshot = json.loads(json.dumps(shown, default=str))["entitlement"]
        assert snapshot["provider_requests_used"] == 1
        assert snapshot["provider_requests_reserved"] == 0
        status, listed = await execute(
            admission_db,
            [
                "entitlement",
                "list",
                "--tenant-id",
                str(tenant_id),
            ],
        )
        assert status == 0 and listed["latestVersion"] == 1
        assert len(listed["entitlements"]) == 1
        summary = await usage.summary(tenant_id=tenant_id)
        assert summary.entitlement_status == "active"
        assert summary.provider_requests_remaining == 1
        async with admission_db.sessions() as session:
            audits = list(
                (
                    await session.scalars(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == tenant_id,
                            AuditEvent.action == "billing.entitlement.configured",
                        )
                    )
                ).all()
            )
            assert len(audits) == 1
            assert audits[0].event_metadata["operator_id"] == "pilot-admin"
            assert audits[0].event_metadata["reason"] == "Approved synthetic pilot"
    finally:
        credential_file.unlink(missing_ok=True)
