from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import event, select

from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.jobs.models import Job, JobEvent
from enterprise_doc_core.presales.models import PresalesAttempt
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import background as background

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("batch", [False, True])
async def test_admission_commits_final_deadline_and_quota_with_seventeen_sql(background, batch):
    b = background
    now = datetime.now(UTC)
    b.service.generation.clock = lambda: now
    usage = b.service.generation.usage_service
    usage.clock = lambda: now
    usage.reservation_ttl = timedelta(seconds=b.settings.row_timeout_seconds + 12)
    packet = await b.service.create(b.context.principal, b.payload, "write-budget-packet")
    row_id = packet.rows[0].id
    url = (
        f"/api/presales/{packet.id}/generate?response=receipt"
        if batch
        else f"/api/presales/{packet.id}/rows/{row_id}/generate?response=receipt"
    )
    kwargs = {"json": {"rowIds": [str(row_id)]}} if batch else {}
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "write-budget-admit"}
    statements = []
    engine = b.sessions.kw["bind"].sync_engine

    def count(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        response = await b.api.post(url, headers=headers, **kwargs)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 202, response.text
    admission = response.json()["admissions"][0]
    assert admission["disposition"] == "enqueued"
    async with b.sessions() as session:
        attempt = (await session.scalars(select(PresalesAttempt))).one()
        reservation = (await session.scalars(select(UsageReservation))).one()
        entitlement = (await session.scalars(select(TenantEntitlement))).one()
        job = (await session.scalars(select(Job))).one()
        job_event = (await session.scalars(select(JobEvent))).one()
        assert attempt.id == UUID(admission["attemptId"]) == reservation.operation_id
        assert attempt.row_id == row_id and attempt.state == "queued"
        assert attempt.deadline_at == now + timedelta(seconds=10)
        assert attempt.created_at == now and attempt.provider_request_count == 0
        assert reservation.expires_at == now + usage.reservation_ttl
        assert reservation.state == "reserved" and reservation.quantity == 1
        assert entitlement.provider_requests_reserved == 1
        assert entitlement.provider_requests_used == 0
        assert job.id == attempt.job_id and job.payload["operation_id"] == str(attempt.id)
        assert job_event.job_id == job.id and job_event.seq == 1
    replay = await b.api.post(url, headers=headers, **kwargs)
    assert replay.status_code == 200
    assert replay.json()["admissions"] == [{**admission, "disposition": "replayed"}]
    assert not b.requests
    assert len(statements) <= 17, statements
