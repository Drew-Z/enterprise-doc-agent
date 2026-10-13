from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.documents.models import DocumentIngestionGeneration, DocumentVersion
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.jobs.models import Job, JobEvent
from enterprise_doc_core.presales.models import PresalesAttempt
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import background as background
from tests.presales.test_presales_background_integration import configured_worker, valid_response

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("batch", [False, True])
async def test_receipt_confirms_committed_work_without_returning_packet_content(background, batch):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "receipt-packet")
    row_id = packet.rows[0].id
    url = (
        f"/api/presales/{packet.id}/generate"
        if batch
        else f"/api/presales/{packet.id}/rows/{row_id}/generate"
    )
    kwargs = {"json": {"rowIds": [str(row_id)]}} if batch else {}
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "receipt-once"}
    statements = []
    engine = b.sessions.kw["bind"].sync_engine

    def observed(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    try:
        response = await b.api.post(url + "?response=receipt", headers=headers, **kwargs)
    finally:
        event.remove(engine, "before_cursor_execute", observed)
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"packetId", "admissions", "rejected"}
    assert body["packetId"] == str(packet.id) and body["rejected"] == []
    admission = body["admissions"][0]
    assert admission == {
        "rowId": str(row_id),
        "disposition": "enqueued",
        "attemptId": admission["attemptId"],
    }
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) < 300
    assert not any("presales_reviews" in statement for statement in statements)
    print(f"Receipt SELECT count: batch={batch}, count={len(statements)}")
    assert len(statements) < 19  # Current full-content admission's measured budget.
    # A separate connection sees all records before the receipt is observed.
    async with b.sessions() as session:
        operation = (await session.scalars(select(PresalesAttempt))).one()
        assert operation.id == UUID(admission["attemptId"])
        assert operation.state == "queued" and operation.provider_request_count == 0
        job = (await session.scalars(select(Job))).one()
        assert job.id == operation.job_id and job.payload["operation_id"] == str(operation.id)
        assert (await session.scalars(select(JobEvent))).one().event_type == "job.created"
        reservation = (await session.scalars(select(UsageReservation))).one()
        assert reservation.operation_id == operation.id and reservation.state == "reserved"
    replay = await b.api.post(url + "?response=receipt", headers=headers, **kwargs)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**body, "admissions": [{**admission, "disposition": "replayed"}]}
    legacy = await b.api.post(url, headers=headers, **kwargs)
    assert legacy.status_code == 202
    view = legacy.json()["packet"] if batch else legacy.json()
    assert view["rows"][0]["attempts"][0]["id"] == admission["attemptId"]
    async with b.sessions() as session:
        assert len((await session.scalars(select(PresalesAttempt))).all()) == 1
        assert len((await session.scalars(select(UsageReservation))).all()) == 1
    assert not b.requests


async def test_receipt_replays_completed_attempt_and_distinguishes_existing_draft(background):
    b = background
    requests = []

    def respond(request):
        requests.append(request)
        return valid_response(request)

    worker = configured_worker(b, respond)
    packet = await b.service.create(b.context.principal, b.payload, "completed-packet")
    row_id = str(packet.rows[0].id)
    url = f"/api/presales/{packet.id}/rows/{row_id}/generate?response=receipt"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "complete-once"}
    first = await b.api.post(url, headers=headers)
    assert first.status_code == 202
    assert await worker.run_once("receipt-worker")
    replay = await b.api.post(url, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["admissions"] == [
        {**first.json()["admissions"][0], "disposition": "replayed"}
    ]
    existing = await b.api.post(url, headers={**headers, "Idempotency-Key": "different-key"})
    assert existing.status_code == 200
    assert existing.json()["admissions"] == [
        {"rowId": row_id, "disposition": "already_drafted", "attemptId": None}
    ]
    read = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    assert read.status_code == 200 and read.json()["rows"][0]["state"] == "drafted"
    async with b.sessions() as session:
        assert len((await session.scalars(select(PresalesAttempt))).all()) == 1
        assert (await session.scalars(select(UsageReservation))).one().state == "consumed"
    assert len(requests) == 1


async def test_batch_receipt_records_partial_rejections_without_discarding_durable_work(background):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "partial-packet")
    missing = str(uuid4())
    row_id = str(packet.rows[0].id)
    response = await b.api.post(
        f"/api/presales/{packet.id}/generate?response=receipt",
        json={"rowIds": [missing, row_id]},
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "partial-once"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["rejected"] == [{"rowId": missing, "code": "presales_not_found"}]
    assert response.json()["admissions"][0]["rowId"] == row_id
    assert response.json()["admissions"][0]["disposition"] == "enqueued"
    assert not b.requests


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "other_tenant",
        "revoked_membership",
        "unavailable_source",
        "stale_source",
        "disabled",
        "short_ttl",
    ],
)
async def test_receipt_rejects_inaccessible_or_uncommittable_work(background, batch, change):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "denied-packet")
    async with b.sessions.begin() as session:
        if change == "revoked_membership":
            member = await session.get(Membership, b.context.membership_id)
            member.is_active = False
        elif change == "unavailable_source":
            generation = await session.get(DocumentIngestionGeneration, b.context.generation_id)
            generation.active = False
        elif change == "stale_source":
            version = await session.get(DocumentVersion, b.context.document_version_id)
            version.version_number += 1
    if change == "disabled":
        b.settings.background_generation_enabled = False
    if change == "short_ttl":
        now = datetime.now(UTC)
        b.service.generation.clock = lambda: now
        b.service.generation.usage_service.clock = lambda: now
        b.service.generation.usage_service.reservation_ttl = timedelta(seconds=92)
    url = (
        f"/api/presales/{packet.id}/generate"
        if batch
        else f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    )
    kwargs = {"json": {"rowIds": [str(packet.rows[0].id)]}} if batch else {}
    token = "other" if change == "other_tenant" else "owner"
    response = await b.api.post(
        url + "?response=receipt",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "denied-once"},
        **kwargs,
    )
    codes = {
        "other_tenant": (404, "presales_not_found"),
        "revoked_membership": (403, "presales_forbidden"),
        "unavailable_source": (404, "presales_source_unavailable"),
        "stale_source": (409, "presales_stale_sources"),
        "disabled": (409, "presales_background_required"),
        "short_ttl": (503, "presales_usage_unavailable"),
    }
    status, code = codes[change]
    if batch and change == "short_ttl":
        assert response.status_code == 200
        assert response.json()["admissions"] == []
        assert response.json()["rejected"] == [{"rowId": str(packet.rows[0].id), "code": code}]
    else:
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code
    async with b.sessions() as session:
        assert not (await session.scalars(select(PresalesAttempt))).all()
        assert not (await session.scalars(select(UsageReservation))).all()
        assert not (await session.scalars(select(Job))).all()
    assert not b.requests


async def test_admitted_work_does_not_bypass_revocation_on_later_read(background):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "revoke-packet")
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "revoke-once"}
    response = await b.api.post(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate?response=receipt",
        headers=headers,
    )
    assert response.status_code == 202
    async with b.sessions.begin() as session:
        generation = await session.get(DocumentIngestionGeneration, b.context.generation_id)
        generation.active = False
    denied = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    assert denied.status_code == 404 and "rows" not in denied.json()
    worker = configured_worker(b, lambda _request: pytest.fail("revoked evidence must not be sent"))
    assert await worker.run_once("revocation-worker")
    async with b.sessions() as session:
        assert (await session.scalars(select(UsageReservation))).one().state == "released"
        assert (await session.scalars(select(PresalesAttempt))).one().state == "failed"
