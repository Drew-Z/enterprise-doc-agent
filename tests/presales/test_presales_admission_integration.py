from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import event, select

from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.documents.models import DocumentIngestionGeneration, DocumentVersion
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.presales.models import PresalesAttempt
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import background as background

pytestmark = pytest.mark.integration


async def test_single_row_batch_admission_has_no_extra_read_budget(background):
    b = background
    packets = []
    for key in ("single", "batch"):
        response = await b.api.post(
            "/api/presales",
            json=b.payload.model_dump(mode="json"),
            headers={"Authorization": "Bearer owner", "Idempotency-Key": key},
        )
        assert response.status_code == 201, response.text
        packets.append(response.json())

    engine = b.sessions.kw["bind"].sync_engine
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    counts = []
    for packet, batch in zip(packets, (False, True), strict=True):
        packet_id, row_id = packet["id"], packet["rows"][0]["id"]
        url = (
            f"/api/presales/{packet_id}/generate"
            if batch
            else f"/api/presales/{packet_id}/rows/{row_id}/generate"
        )
        kwargs = {"json": {"rowIds": [row_id]}} if batch else {}
        headers = {"Authorization": "Bearer owner", "Idempotency-Key": "admit-once"}
        statements.clear()
        event.listen(engine, "before_cursor_execute", observed)
        try:
            accepted = await b.api.post(url, headers=headers, **kwargs)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        counts.append(len(statements))
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        view = body["packet"] if batch else body
        assert view["rows"][0]["state"] == "queued"
        if batch:
            assert body["rejected"] == []
        assert accepted.headers["cache-control"] == "no-store"
        replay = await b.api.post(url, headers=headers, **kwargs)
        assert replay.status_code == 202
        assert replay.json() == body

        async with b.sessions() as session:
            operation = (
                await session.scalars(
                    select(PresalesAttempt).where(PresalesAttempt.row_id == UUID(row_id))
                )
            ).one()
            job = await session.get(Job, operation.job_id)
            assert job is not None and job.payload["operation_id"] == str(operation.id)
            reservation = (
                await session.scalars(
                    select(UsageReservation).where(UsageReservation.operation_id == operation.id)
                )
            ).one()
            assert reservation.state == "reserved"
            assert operation.provider_request_count == 0
    assert not b.requests
    print(f"Admission SELECT counts: single={counts[0]}, batch={counts[1]}")
    assert 0 < counts[1] <= counts[0], counts


@pytest.mark.parametrize("background_enabled", [True, False])
@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ("other_tenant", 404, "presales_not_found"),
        ("revoked_membership", 403, "presales_forbidden"),
        ("unavailable_source", 404, "presales_source_unavailable"),
        ("stale_source", 409, "presales_stale_sources"),
    ],
)
async def test_batch_access_errors_precede_admission_and_disabled_mode(
    background, background_enabled, change, status, code
):
    b = background
    b.settings.background_generation_enabled = background_enabled
    packet = await b.service.create(b.context.principal, b.payload, "restricted-packet")
    async with b.sessions.begin() as session:
        if change == "revoked_membership":
            membership = await session.get(Membership, b.context.membership_id)
            membership.is_active = False
        elif change == "unavailable_source":
            generation = await session.get(DocumentIngestionGeneration, b.context.generation_id)
            generation.active = False
        elif change == "stale_source":
            version = await session.get(DocumentVersion, b.context.document_version_id)
            version.version_number += 1

    token = "other" if change == "other_tenant" else "owner"
    response = await b.api.post(
        f"/api/presales/{packet.id}/generate",
        json={"rowIds": [str(packet.rows[0].id)]},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "restricted-batch"},
    )
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert not b.requests
    async with b.sessions() as session:
        assert not (await session.scalars(select(PresalesAttempt))).all()
        assert not (await session.scalars(select(UsageReservation))).all()
        assert not (await session.scalars(select(Job).where(Job.type == "presales.generate"))).all()
