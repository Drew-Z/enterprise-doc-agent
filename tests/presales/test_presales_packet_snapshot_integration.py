from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.presales.models import PresalesRow
from enterprise_doc_core.presales.schemas import ReviewInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_workflow_integration import workspace as workspace

pytestmark = pytest.mark.integration


async def test_completion_during_packet_read_never_reports_failure(workspace, monkeypatch):
    service, _, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "snapshot-create")
    gateway.release.clear()
    generation = asyncio.create_task(
        service.generation.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    )
    original_execute = AsyncSession.execute
    completed_between_reads = False

    async def execute(session, statement, *args, **kwargs):
        nonlocal completed_between_reads
        result = await original_execute(session, statement, *args, **kwargs)
        if not completed_between_reads and any(
            column.get("entity") is PresalesRow
            for column in getattr(statement, "column_descriptions", ())
        ):
            completed_between_reads = True
            gateway.release.set()
            await asyncio.wait_for(asyncio.shield(generation), 10)
        return result

    try:
        await asyncio.wait_for(gateway.entered.wait(), 10)
        monkeypatch.setattr(AsyncSession, "execute", execute)
        observed = await service.get(context.principal, packet.id)
        assert completed_between_reads
        assert observed.rows[0].state in {"running", "drafted"}
        current = await service.get(context.principal, packet.id)
        assert current.rows[0].state == "drafted"
        assert current.rows[0].draft is not None
        assert current.rows[0].attempts[-1].state == "succeeded"
        assert len(gateway.calls) == 1
    finally:
        gateway.release.set()
        await generation


async def test_review_during_packet_read_keeps_revision_and_history_consistent(
    workspace, monkeypatch
):
    service, _, context, _, _, payload = workspace
    packet = await service.create(context.principal, payload, "review-snapshot-create")
    packet = await service.generate(
        context.principal, packet.id, packet.rows[0].id, "review-snapshot-generate"
    )
    original_execute = AsyncSession.execute
    reviewed_between_reads = False

    async def execute(session, statement, *args, **kwargs):
        nonlocal reviewed_between_reads
        result = await original_execute(session, statement, *args, **kwargs)
        if not reviewed_between_reads and any(
            column.get("entity") is PresalesRow
            for column in getattr(statement, "column_descriptions", ())
        ):
            reviewed_between_reads = True
            await service.review(
                context.principal,
                packet.id,
                packet.rows[0].id,
                ReviewInput(
                    expected_revision=1,
                    status="conflicting_evidence",
                    answer="Reviewed retention conflict",
                    missing_information=["Confirm applicable retention"],
                    note="Concurrent review regression",
                ),
                "review-snapshot-save",
            )
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)
    observed = await service.get(context.principal, packet.id)
    assert reviewed_between_reads
    row = observed.rows[0]
    assert row.revision == 1
    assert row.review is None
    assert not row.review_history
    current = await service.get(context.principal, packet.id)
    assert current.rows[0].revision == 2
    assert current.rows[0].review.revision == 2
    assert len(current.rows[0].review_history) == 1
