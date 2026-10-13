from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select, text

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.presales.models import PresalesAttempt
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import background as background

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("batch", [False, True])
async def test_admission_does_not_wait_for_unrelated_tenant_event(background, batch):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "lock-packet")
    url = (
        f"/api/presales/{packet.id}/generate"
        if batch
        else f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    )
    kwargs = {"json": {"rowIds": [str(packet.rows[0].id)]}} if batch else {}
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "lock-admission"}
    # The actual insert holds a foreign-key KEY SHARE lock on Tenant until
    # commit, as a worker writing another operation's event would do.
    async with b.sessions.begin() as writer:
        await append_audit_event(
            writer,
            tenant_id=b.context.tenant_id,
            actor_id=b.context.actor_id,
            action="integration.unrelated_event",
            resource_type="integration",
        )
        async with asyncio.timeout(2):
            response = await b.api.post(url, headers=headers, **kwargs)
            replay = await b.api.post(url, headers=headers, **kwargs)
        assert response.status_code == 202, response.text
        view = response.json()["packet"] if batch else response.json()
        assert view["rows"][0]["state"] == "queued"
        assert replay.status_code == 202
        assert replay.json() == response.json()
    async with b.sessions() as session:
        attempt = (
            await session.scalars(
                select(PresalesAttempt).where(PresalesAttempt.row_id == packet.rows[0].id)
            )
        ).one()
        job = await session.get(Job, attempt.job_id)
        assert job is not None and job.payload["operation_id"] == str(attempt.id)
        reservation = (
            await session.scalars(
                select(UsageReservation).where(UsageReservation.operation_id == attempt.id)
            )
        ).one()
        assert reservation.state == "reserved"
        assert UUID(view["rows"][0]["attempts"][0]["id"]) == attempt.id
    assert not b.requests


async def test_admission_waits_for_tenant_deactivation_and_rechecks_access(background):
    b = background
    packet = await b.service.create(b.context.principal, b.payload, "deactivate-packet")
    request = None
    try:
        async with b.sessions.begin() as writer:
            blocker = await writer.scalar(text("SELECT pg_backend_pid()"))
            tenant = await writer.get(Tenant, b.context.tenant_id)
            tenant.is_active = False
            await writer.flush()
            request = asyncio.create_task(
                b.api.post(
                    f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
                    headers={"Authorization": "Bearer owner", "Idempotency-Key": "deactivate"},
                )
            )
            # Observe a real blocked database request rather than assuming that
            # an asyncio scheduling delay proves the admission lock is retained.
            async with asyncio.timeout(2), b.sessions() as observer:
                while not await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)) "
                        "AND datname=current_database()"
                    ),
                    {"blocker": blocker},
                ):
                    assert not request.done(), "Admission bypassed the tenant update"
                    await asyncio.sleep(0.01)
            assert not request.done()
        async with asyncio.timeout(2):
            response = await request
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "presales_forbidden"
    finally:
        if request is not None:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    async with b.sessions() as session:
        assert not (await session.scalars(select(PresalesAttempt))).all()
        assert not (await session.scalars(select(UsageReservation))).all()
    assert not b.requests
