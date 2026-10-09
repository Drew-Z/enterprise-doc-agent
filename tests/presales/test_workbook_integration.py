import base64
from importlib import import_module
from io import BytesIO
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from openpyxl import load_workbook
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.models import PresalesPacket
from enterprise_doc_core.presales.schemas import ReviewInput, WorkbookImportInput
from enterprise_doc_core.presales.workbook import inspect_workbook
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_workflow_integration import workspace as workspace
from tests.presales.test_workbook import mapping, questionnaire

pytestmark = pytest.mark.integration


async def test_workbook_import_replay_reload_review_and_original_download(workspace):
    service, sessions, context, other, gateway, payload = workspace
    content = questionnaire(13)
    request = WorkbookImportInput(
        filename="customer.xlsx",
        content_base64=base64.b64encode(content).decode(),
        title="Customer questionnaire",
        sources=payload.sources,
        mapping=mapping(13),
        confirmed_sha256=inspect_workbook(content, "customer.xlsx").sha256,
    )
    packet = await service.import_workbook(context.principal, request, "import-key")
    assert len(packet.rows) == 13
    assert packet.workbook.rows == list(range(2, 15))
    assert not gateway.calls
    replay = await service.import_workbook(context.principal, request, "import-key")
    assert replay.id == packet.id
    with pytest.raises(PresalesError, match="presales_idempotency_conflict"):
        await service.import_workbook(
            context.principal, request.model_copy(update={"title": "Different"}), "import-key"
        )
    with pytest.raises(PresalesError, match="presales_not_found"):
        await service.export_workbook(other.principal, packet.id, "draft")
    packet = await service.generate(context.principal, packet.id, packet.rows[0].id, "first-row")
    packet = await service.review(
        context.principal,
        packet.id,
        packet.rows[0].id,
        ReviewInput(
            expected_revision=1,
            status="conflicting_evidence",
            answer="Reviewed customer response",
            missing_information=["Confirm period"],
        ),
        "review-first",
    )
    reloaded = await service.get(context.principal, packet.id)
    assert reloaded.workbook == packet.workbook
    content = await service.export_workbook(context.principal, packet.id, "draft")
    book = load_workbook(BytesIO(content))
    assert "Reviewed customer response" in book["技术要求"]["C2"].value
    assert "尚未完成生成" in book["技术要求"]["C14"].value
    with pytest.raises(PresalesError, match="presales_review_required"):
        await service.export_workbook(context.principal, packet.id, "reviewed")
    async with sessions() as session:
        stored = (
            await session.scalars(select(PresalesPacket).where(PresalesPacket.id == packet.id))
        ).one()
        assert "workbook_content" not in stored.__dict__


def import_request(payload, count=2):
    content = questionnaire(count)
    return WorkbookImportInput(
        filename="customer.xlsx",
        content_base64=base64.b64encode(content).decode(),
        title="Customer questionnaire",
        sources=payload.sources,
        mapping=mapping(count),
        confirmed_sha256=inspect_workbook(content, "customer.xlsx").sha256,
    )


async def test_workbook_migration_roundtrip_and_refuses_to_erase_history(workspace):
    service, sessions, context, _, _, payload = workspace
    module = import_module(
        "enterprise_doc_core.db.migrations.versions.20261009_0035_presales_workbook"
    )

    def migrate(connection, direction):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            getattr(module, direction)()

    engine = sessions.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "downgrade")
        await connection.run_sync(migrate, "upgrade")
    packet = await service.import_workbook(context.principal, import_request(payload), "migration")
    with pytest.raises(RuntimeError, match="presales_workbook_history_present"):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "downgrade")
    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            await session.execute(
                update(PresalesPacket)
                .where(PresalesPacket.id == packet.id)
                .values(workbook_content=None)
            )
    assert (await service.get(context.principal, packet.id)).workbook is not None


async def test_workbook_storage_limit_replay_and_membership_revocation(workspace):
    service, sessions, context, _, gateway, payload = workspace
    request = import_request(payload)
    packet = await service.import_workbook(context.principal, request, "first")
    async with sessions.begin() as session:
        stored = await session.get(PresalesPacket, packet.id)
        for number in range(10):
            session.add(
                PresalesPacket(
                    id=uuid4(),
                    tenant_id=stored.tenant_id,
                    actor_id=stored.actor_id,
                    title="Owned storage fixture",
                    idempotency_key=f"storage-{number}",
                    fingerprint="a" * 64,
                    sources=stored.sources,
                    workbook_metadata=stored.workbook_metadata,
                    workbook_content=b"x" * (2 * 1024 * 1024),
                )
            )
    assert (await service.import_workbook(context.principal, request, "first")).id == packet.id
    with pytest.raises(PresalesError, match="presales_workbook_storage_limit"):
        await service.import_workbook(context.principal, request, "over-limit")
    async with sessions.begin() as session:
        await session.execute(
            update(Membership)
            .where(
                Membership.tenant_id == context.tenant_id, Membership.user_id == context.actor_id
            )
            .values(is_active=False)
        )
    with pytest.raises(PresalesError, match="presales_forbidden"):
        await service.export_workbook(context.principal, packet.id, "draft")
    with pytest.raises(PresalesError, match="presales_forbidden"):
        await service.import_workbook(context.principal, request, "first")
    assert not gateway.calls
