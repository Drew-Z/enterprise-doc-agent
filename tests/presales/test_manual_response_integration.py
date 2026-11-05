import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func, select, update

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.models import PresalesAttempt
from enterprise_doc_core.presales.schemas import ManualResponseInput, ReviewInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_workflow_integration import workspace as workspace
from tests.presales.test_workbook_integration import import_request

pytestmark = pytest.mark.integration


async def test_pending_manual_response_survives_review_and_original_file_export(
    workspace, monkeypatch
):
    service, sessions, context, _, gateway, payload = workspace

    async def forbidden_embedding(*args, **kwargs):
        raise AssertionError("Manual completion must not call embeddings")

    monkeypatch.setattr(
        service.generation.retriever.embedding_provider, "embed", forbidden_embedding
    )
    async with sessions.begin() as session:
        await session.execute(
            update(TenantEntitlement)
            .where(TenantEntitlement.tenant_id == context.tenant_id)
            .values(
                period_start=datetime.now(UTC) - timedelta(days=2),
                period_end=datetime.now(UTC) - timedelta(days=1),
            )
        )
    packet = await service.import_workbook(context.principal, import_request(payload, 1), "manual")
    evidence = await service.manual_evidence(
        context.principal, packet.id, payload.sources[0].version_id, "Retention", 0
    )
    request = ManualResponseInput(
        expected_revision=0,
        status="supported",
        answer="人工核对后确认保留30天。",
        citations=[
            evidence.items[0].model_dump(include={"chunk_id", "document_version_id", "excerpt"})
        ],
        prerequisites=[],
        note="已核对原文适用范围",
    )
    saved = await service.manual_response(
        context.principal, packet.id, packet.rows[0].id, request, "save"
    )
    row = saved.rows[0]
    assert row.draft.answer == request.answer and row.revision == 1
    assert row.manual_authorship.actor_id == context.actor_id
    assert row.manual_authorship.note == request.note
    assert not row.attempts and not row.review_history
    assert row.draft.retrieval == []
    replay = await service.manual_response(context.principal, packet.id, row.id, request, "save")
    assert replay.rows[0] == row
    reviewed = await service.review(
        context.principal,
        packet.id,
        row.id,
        ReviewInput(
            expected_revision=1,
            status=request.status,
            answer=request.answer,
            prerequisites=[],
            note="人工复核",
        ),
        "review",
    )
    assert reviewed.rows[0].draft == row.draft
    assert (await service.get(context.principal, packet.id)).rows[0] == reviewed.rows[0]
    assert (
        await service.manual_response(context.principal, packet.id, row.id, request, "save")
    ).rows[0] == reviewed.rows[0]
    exported = await service.export_workbook(context.principal, packet.id, "reviewed")
    assert request.answer in load_workbook(io.BytesIO(exported))["技术要求"]["C2"].value
    rows = list(
        csv.DictReader(
            io.StringIO(
                (await service.export(context.principal, packet.id, "reviewed")).decode("utf-8-sig")
            )
        )
    )
    assert rows[0]["原模型文案"] == ""
    assert rows[0]["初稿来源"] == "人工填写"
    assert rows[0]["原人工文案"] == request.answer
    assert not gateway.calls
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PresalesAttempt)) == 0
        assert await session.scalar(select(func.count()).select_from(UsageReservation)) == 0
        events = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "presales.row.manually_authored")
            )
        ).all()
        assert len(events) == 1
        assert request.answer not in str(events[0].event_metadata)


@pytest.mark.parametrize("state", ["queued", "recovering"])
async def test_manual_response_refuses_durable_active_generation(workspace, state):
    service, sessions, context, _, gateway, payload = workspace
    service.generation.settings.background_generation_enabled = True
    packet = await service.create(context.principal, payload, "durable")
    queued = await service.generate(context.principal, packet.id, packet.rows[0].id, "queued")
    async with sessions.begin() as session:
        await session.execute(
            update(PresalesAttempt)
            .where(PresalesAttempt.id == queued.rows[0].attempts[0].id)
            .values(state=state)
        )
    with pytest.raises(PresalesError, match="presales_generation_busy"):
        await service.manual_response(
            context.principal, packet.id, packet.rows[0].id, insufficient(), "manual"
        )
    assert (await service.get(context.principal, packet.id)).rows[0].draft is None
    assert not gateway.calls
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PresalesAttempt)) == 1
        reservation = (await session.scalars(select(UsageReservation))).one()
        assert reservation.state == "reserved"


async def test_running_generation_cannot_be_overwritten_and_failure_history_is_preserved(workspace):
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "active")
    row_id = packet.rows[0].id
    gateway.release.clear()
    gateway.fail_next = True
    running = asyncio.create_task(
        service.generate(context.principal, packet.id, row_id, "original")
    )
    try:
        await asyncio.wait_for(gateway.entered.wait(), 10)
        with pytest.raises(PresalesError, match="presales_generation_busy"):
            await service.manual_response(
                context.principal, packet.id, row_id, insufficient(), "manual"
            )
    finally:
        gateway.release.set()
        failed = await running
    before = failed.rows[0].attempts
    assert before[0].state == "failed"
    saved = await service.manual_response(
        context.principal, packet.id, row_id, insufficient(), "manual"
    )
    assert saved.rows[0].attempts == before
    assert saved.rows[0].manual_authorship is not None
    assert (await service.generate(context.principal, packet.id, row_id, "no-regeneration")).rows[
        0
    ] == saved.rows[0]
    assert len(gateway.calls) == 1
    async with sessions() as session:
        reservation = (await session.scalars(select(UsageReservation))).one()
        assert reservation.state == "released"


async def test_manual_concurrent_saves_and_migration_history_guard(workspace):
    service, sessions, context, _, gateway, payload = workspace
    module = import_module(
        "enterprise_doc_core.db.migrations.versions.20261009_0036_presales_manual_authorship"
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
    packet = await service.create(context.principal, payload, "race")
    results = await asyncio.gather(
        *[
            service.manual_response(
                context.principal, packet.id, packet.rows[0].id, insufficient(), key
            )
            for key in ["first", "second"]
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, PresalesError) for result in results) == 1
    error = next(result for result in results if isinstance(result, PresalesError))
    assert error.code == "presales_revision_conflict"
    with pytest.raises(RuntimeError, match="presales_manual_history_present"):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "downgrade")
    assert (await service.get(context.principal, packet.id)).rows[0].manual_authorship is not None
    assert not gateway.calls


async def test_manual_evidence_rejects_wrong_version_foreign_chunk_and_nonliteral_quote(workspace):
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "evidence")
    page = await service.manual_evidence(
        context.principal, packet.id, payload.sources[0].version_id
    )
    citation = page.items[0].model_dump(include={"chunk_id", "document_version_id", "excerpt"})
    for replacement in [
        {"chunk_id": uuid4()},
        {"document_version_id": payload.sources[1].version_id},
        {"excerpt": "fabricated text"},
        {"excerpt": "%"},
    ]:
        proposed = ManualResponseInput(
            expected_revision=0,
            status="supported",
            answer="人工确认",
            note="人工核查",
            citations=[{**citation, **replacement}],
        )
        with pytest.raises(PresalesError, match="presales_manual_evidence_invalid"):
            await service.manual_response(
                context.principal, packet.id, packet.rows[0].id, proposed, str(uuid4())
            )
    assert (await service.get(context.principal, packet.id)).rows[0].draft is None
    assert not (
        await service.manual_evidence(
            context.principal, packet.id, payload.sources[0].version_id, "%"
        )
    ).items
    from enterprise_doc_core.documents.models import DocumentIngestionGeneration

    async with sessions.begin() as session:
        await session.execute(
            update(DocumentIngestionGeneration)
            .where(DocumentIngestionGeneration.id == context.generation_id)
            .values(active=False)
        )
    with pytest.raises(PresalesError, match="presales_source_unavailable"):
        await service.manual_response(
            context.principal, packet.id, packet.rows[0].id, insufficient(), "stale"
        )
    assert not gateway.calls
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PresalesAttempt)) == 0
        assert await session.scalar(select(func.count()).select_from(UsageReservation)) == 0


def insufficient():
    return ManualResponseInput(
        expected_revision=0,
        status="insufficient_evidence",
        answer="尚无证明",
        missing_information=["请提供证明"],
        prerequisites=[],
        note="人工核查",
    )


async def test_manual_http_authorization_validation_and_private_metadata(workspace):
    service, sessions, context, other, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "http")

    class Resolver:
        async def resolve(self, token):
            return context.principal if token == "owner" else other.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        route = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/manual-response"
        evidence_route = f"/api/presales/{packet.id}/manual-evidence"
        headers = {"Authorization": "Bearer owner", "Idempotency-Key": "manual-http"}
        params = {"versionId": str(payload.sources[0].version_id), "query": "Retention"}
        evidence = await client.get(evidence_route, params=params, headers=headers)
        assert evidence.status_code == 200, evidence.text
        assert evidence.headers["cache-control"] == "no-store"
        assert "Retention" in evidence.json()["items"][0]["excerpt"]
        assert (
            await client.get(
                evidence_route, params=params, headers={"Authorization": "Bearer other"}
            )
        ).status_code == 404
        body = insufficient().model_dump(mode="json", by_alias=True)
        assert (
            await client.put(route, json={**body, "actorId": str(other.actor_id)}, headers=headers)
        ).status_code == 422
        assert (
            await client.put(route, json={**body, "status": "supported"}, headers=headers)
        ).status_code == 422
        assert (
            await client.put(route, json=body, headers={**headers, "Authorization": "Bearer other"})
        ).status_code == 404
        response = await client.put(route, json=body, headers=headers)
        assert response.status_code == 200, response.text
        authorship = response.json()["rows"][0]["manualAuthorship"]
        assert set(authorship) == {"actorId", "createdAt", "note"}
        assert authorship["actorId"] == str(context.actor_id)
        assert (await client.put(route, json=body, headers=headers)).json() == response.json()
        assert (
            await client.put(route, json={**body, "answer": "changed"}, headers=headers)
        ).status_code == 409
        async with sessions.begin() as session:
            await session.execute(
                update(Membership)
                .where(Membership.tenant_id == context.tenant_id)
                .values(is_active=False)
            )
        assert (await client.put(route, json=body, headers=headers)).status_code == 403
        assert (await client.get(evidence_route, params=params, headers=headers)).status_code == 403
        assert (
            await client.get(f"/api/presales/{packet.id}/export", headers=headers)
        ).status_code == 403
    assert not gateway.calls
