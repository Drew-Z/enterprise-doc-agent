from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.documents.models import DocumentIngestionGeneration, DocumentVersion
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.jobs.models import Job, JobEvent
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
            job_events = (
                await session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
            ).all()
            assert [(item.seq, item.event_type) for item in job_events] == [(1, "job.created")]
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
    assert counts[0] <= 19, counts


@pytest.mark.parametrize("ttl", [92, 93, 1200])
async def test_admission_uses_durable_reservation_expiry_and_rolls_back_if_too_short(
    background, ttl
):
    b = background
    now = datetime.now(UTC)
    b.service.generation.clock = lambda: now
    usage = b.service.generation.usage_service
    usage.clock = lambda: now
    usage.reservation_ttl = timedelta(seconds=ttl)
    packet = await b.service.create(b.context.principal, b.payload, "expiry-packet")
    response = await b.api.post(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "expiry-admit"},
    )
    async with b.sessions() as session:
        attempts = (await session.scalars(select(PresalesAttempt))).all()
        reservations = (await session.scalars(select(UsageReservation))).all()
        jobs = (await session.scalars(select(Job).where(Job.type == "presales.generate"))).all()
        entitlement = (await session.scalars(select(TenantEntitlement))).one()
        if ttl == 92:
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "presales_usage_unavailable"
            assert not attempts and not reservations and not jobs
            assert entitlement.provider_requests_reserved == 0
        else:
            assert response.status_code == 202, response.text
            assert len(attempts) == len(reservations) == len(jobs) == 1
            assert reservations[0].expires_at == now + timedelta(seconds=ttl)
            assert attempts[0].deadline_at == now + timedelta(seconds=min(900, ttl - 92))
            assert entitlement.provider_requests_reserved == 1
            replay = await usage.reserve_provider_request(
                tenant_id=b.context.tenant_id, operation_id=attempts[0].id
            )
            assert replay.replay and replay.expires_at == reservations[0].expires_at
    assert not b.requests


@pytest.mark.parametrize(
    ("state", "prior_day", "expired", "daily_limit", "queue_limit", "expected"),
    [
        ("failed", False, False, 1, 2, "presales_daily_limit"),
        ("queued", False, False, 1, 1, "presales_daily_limit"),
        ("running", True, False, 1, 1, "presales_generation_busy"),
        ("recovering", True, False, 1, 1, "presales_generation_busy"),
        ("queued", False, True, 2, 1, None),
        ("failed", True, False, 1, 1, None),
    ],
)
async def test_admission_counter_boundaries(
    background, state, prior_day, expired, daily_limit, queue_limit, expected
):
    b = background
    now = datetime.now(UTC)
    b.service.generation.clock = lambda: now
    b.settings.daily_attempt_limit = daily_limit
    b.settings.queued_attempt_limit = queue_limit
    packet = await b.service.create(b.context.principal, b.payload, "prior-packet")
    async with b.sessions.begin() as session:
        session.add(
            PresalesAttempt(
                id=uuid4(),
                tenant_id=b.context.tenant_id,
                row_id=packet.rows[0].id,
                number=1,
                idempotency_key="prior-operation",
                state=state,
                model_provider="openai_compatible",
                model_name="test-model",
                provenance={},
                deadline_at=now + timedelta(seconds=-1 if expired else 60),
                created_at=(
                    now.replace(hour=0, minute=0, second=0, microsecond=0)
                    - timedelta(microseconds=1)
                )
                if prior_day
                else now,
            )
        )
    target = await b.service.create(b.context.principal, b.payload, "target-packet")
    response = await b.api.post(
        f"/api/presales/{target.id}/rows/{target.rows[0].id}/generate",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "target-admit"},
    )
    if expected:
        assert response.status_code in (409, 429), response.text
        assert response.json()["error"]["code"] == expected
    else:
        assert response.status_code == 202, response.text
    async with b.sessions() as session:
        assert len((await session.scalars(select(UsageReservation))).all()) == (
            0 if expected else 1
        )
        assert len((await session.scalars(select(PresalesAttempt))).all()) == (1 if expected else 2)
    assert not b.requests


async def test_concurrent_single_row_admission_cannot_exceed_queue_capacity(background):
    b = background
    b.settings.queued_attempt_limit = 1
    packets = [
        await b.service.create(b.context.principal, b.payload, key) for key in ("race-a", "race-b")
    ]
    responses = await asyncio.gather(
        *[
            b.api.post(
                f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
                headers={"Authorization": "Bearer owner", "Idempotency-Key": "race-admit"},
            )
            for packet in packets
        ]
    )
    assert sorted(response.status_code for response in responses) == [202, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "presales_generation_busy"
    async with b.sessions() as session:
        assert len((await session.scalars(select(PresalesAttempt))).all()) == 1
        assert len((await session.scalars(select(UsageReservation))).all()) == 1
    assert not b.requests


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
