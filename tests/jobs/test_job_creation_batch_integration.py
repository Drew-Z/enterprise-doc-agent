from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from tests.agent.test_agent_run_integration import _seed_agent_context
from tests.browser_sessions.conftest import browser_db as browser_db

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.jobs.models import Job, JobEvent, OutboxEvent
from enterprise_doc_core.jobs.service import JobIdempotencyConflict, create_job_records

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("outbox_type", ["document.ingest.requested", None])
async def test_new_job_persists_its_initial_records_in_two_database_round_trips(
    browser_db, outbox_type
):
    db = browser_db
    owner = await _seed_agent_context(db.sessions)
    available_at = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", observed)
    try:
        async with db.sessions.begin() as session:
            created = await create_job_records(
                session,
                tenant_id=owner.tenant_id,
                actor_id=owner.actor_id,
                job_type="document.ingest" if outbox_type else "presales.generate",
                idempotency_key="atomic-create",
                payload={"document_version_id": str(owner.document_version_id)},
                document_version_id=owner.document_version_id,
                outbox_event_type=outbox_type,
                request_id="batch-request",
                correlation_id="batch-correlation",
                available_at=available_at,
                max_attempts=7,
                priority=3,
            )
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", observed)
    assert len(statements) <= 2, [s.split()[0] for s in statements]
    async with db.sessions() as session:
        job = await session.get(Job, created.job_id)
        events = (
            await session.scalars(select(JobEvent).where(JobEvent.job_id == created.job_id))
        ).all()
        audits = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.resource_id == created.job_id)
            )
        ).all()
        outbox = (
            await session.scalars(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == created.job_id)
            )
        ).all()
        assert not created.replayed
        assert job is not None
        assert job.status == "pending" and job.attempts == 0 and job.max_attempts == 7
        assert job.available_at == available_at and job.priority == 3
        assert job.payload_version == job.version == 1 and job.fencing_token == 0
        assert job.tenant_id == owner.tenant_id and job.actor_id == owner.actor_id
        assert job.payload == {"document_version_id": str(owner.document_version_id)}
        assert job.document_version_id == owner.document_version_id
        assert len(events) == len(audits) == 1
        assert events[0].seq == 1 and events[0].event_type == "job.created"
        assert events[0].status == "pending" and events[0].actor_id == owner.actor_id
        assert events[0].tenant_id == owner.tenant_id
        assert events[0].payload == {"job_type": job.type} and events[0].payload_version == 1
        assert audits[0].action == "job.created" and audits[0].request_id == "batch-request"
        assert audits[0].correlation_id == "batch-correlation"
        assert audits[0].event_metadata == {
            "event_type": "job.created",
            "status": "pending",
            "job_type": job.type,
        }
        assert audits[0].tenant_id == owner.tenant_id and audits[0].actor_id == owner.actor_id
        assert audits[0].schema_version == 1 and audits[0].resource_type == "job"
        assert events[0].created_at == audits[0].occurred_at == job.created_at
        assert len(outbox) == (1 if outbox_type else 0)
        assert created.outbox_event_id == (outbox[0].id if outbox else None)
        if outbox:
            assert outbox[0].event_type == outbox_type and outbox[0].status == "pending"
            assert outbox[0].attempts == 0 and outbox[0].payload_version == 1
            assert outbox[0].available_at == available_at
            assert outbox[0].payload == {
                "job_id": str(created.job_id),
                "tenant_id": str(owner.tenant_id),
                "document_version_id": str(owner.document_version_id),
            }


@pytest.mark.parametrize("outbox_type", ["document.ingest.requested", None])
async def test_initial_group_replay_and_conflict_preserve_original_records(browser_db, outbox_type):
    owner = await _seed_agent_context(browser_db.sessions)
    command = dict(
        tenant_id=owner.tenant_id,
        actor_id=owner.actor_id,
        job_type="presales.generate",
        idempotency_key="same-command",
        payload={"value": "original"},
        outbox_event_type=outbox_type,
    )
    async with browser_db.sessions.begin() as session:
        first = await create_job_records(session, **command)
    async with browser_db.sessions.begin() as session:
        replay = await create_job_records(session, **command, request_id="replay-request")
    assert replay.replayed and replay.job_id == first.job_id
    assert replay.outbox_event_id == first.outbox_event_id
    with pytest.raises(JobIdempotencyConflict):
        async with browser_db.sessions.begin() as session:
            await create_job_records(session, **(command | {"payload": {"value": "changed"}}))
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
        assert await session.scalar(select(func.count()).select_from(JobEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == (
            1 if outbox_type else 0
        )
        audit = await session.scalar(select(AuditEvent))
        assert audit.request_id is None


@pytest.mark.parametrize("failed_table", ["jobs", "job_events", "audit_events", "outbox_events"])
async def test_database_write_failure_leaves_no_partial_initial_group(browser_db, failed_table):
    owner = await _seed_agent_context(browser_db.sessions)
    async with browser_db.engine.begin() as connection:
        # A real PostgreSQL constraint rejects one write in this fixture's schema.
        # NOT VALID keeps fixture rows intact; every new row is still checked.
        await connection.execute(
            text(
                f'ALTER TABLE "{failed_table}" ADD CONSTRAINT reject_initial_write '
                "CHECK (false) NOT VALID"
            )
        )
    with pytest.raises(IntegrityError):
        async with browser_db.sessions.begin() as session:
            await create_job_records(
                session,
                tenant_id=owner.tenant_id,
                actor_id=owner.actor_id,
                job_type="document.ingest",
                idempotency_key="rejected-group",
                payload={},
            )
    async with browser_db.sessions() as session:
        for model in (Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_caller_rollback_includes_initial_group(browser_db):
    owner = await _seed_agent_context(browser_db.sessions)
    async with browser_db.sessions() as session:
        async with session.begin():
            created = await create_job_records(
                session,
                tenant_id=owner.tenant_id,
                actor_id=owner.actor_id,
                job_type="presales.generate",
                idempotency_key="outer-rollback",
                payload={},
            )
            assert await session.get(Job, created.job_id) is not None
            await session.rollback()
    async with browser_db.sessions() as session:
        for model in (Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_unflushed_parents_and_cached_creation_get_distinct_record_ids(browser_db):
    tenant_id, actor_id = uuid4(), uuid4()
    async with browser_db.sessions.begin() as session:
        with session.no_autoflush:
            session.add(
                Tenant(
                    id=tenant_id,
                    name="Pending tenant",
                    slug=f"pending-{tenant_id}",
                    quota_bytes=1024,
                )
            )
            session.add(User(id=actor_id, email=f"pending-{actor_id}@example.test"))
            created = [
                await create_job_records(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    job_type="document.ingest",
                    idempotency_key=f"pending-{index}",
                    payload={"index": index},
                )
                for index in range(2)
            ]
    assert created[0].job_id != created[1].job_id
    assert created[0].outbox_event_id != created[1].outbox_event_id
    async with browser_db.sessions() as session:
        for model in (Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 2
        jobs = (await session.scalars(select(Job))).all()
        assert all(job.available_at is not None for job in jobs)
        assert all(job.document_version_id is None for job in jobs)
        outboxes = (await session.scalars(select(OutboxEvent))).all()
        assert {row.aggregate_id for row in outboxes} == {row.job_id for row in created}
        assert all(row.payload["document_version_id"] is None for row in outboxes)
