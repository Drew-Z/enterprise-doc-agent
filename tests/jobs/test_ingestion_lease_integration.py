from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from tests.jobs.test_m3_ingestion_integration import FakeObjectStore, _seed_uploaded_document
from tests.migration_database import migration_database

from enterprise_doc_core.db import create_session_factory
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
    IngestionVersions,
)
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.structure_models import DocumentStructure
from enterprise_doc_core.jobs import Job, JobRuntimeService, create_job_records

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _ingestion_fixture(*, repetitions=15):
    async with migration_database() as database:
        async with database.sessions() as session:
            schema = await session.scalar(text("SELECT current_schema()"))
        # One handler plus its heartbeat, with no unbounded overflow.
        engine = create_async_engine(
            database.engine.url,
            connect_args={"options": f"-csearch_path={schema},public"},
            pool_size=2,
            max_overflow=0,
            pool_timeout=0.5,
        )
        sessions = create_session_factory(engine)
        content = b"# Price\nThe monthly price is 12 USD per user.\n" * repetitions
        tenant, actor, version, _ = await _seed_uploaded_document(sessions, content=content)
        async with sessions.begin() as session:
            created = await create_job_records(
                session,
                tenant_id=tenant,
                actor_id=actor,
                job_type="document.ingest",
                idempotency_key="lease-regression",
                document_version_id=version,
                payload={"document_version_id": str(version), "parser_version": 2},
            )
        runtime = JobRuntimeService(session_factory=sessions, lease_seconds=60)
        claim = await runtime.claim(job_id=created.job_id, worker_id="ingestion-lease-test")
        assert claim is not None
        store = FakeObjectStore(content)
        service = DocumentIngestionService(
            session_factory=sessions,
            object_store=store,
            documents_bucket="documents",
            embedding_provider=HashEmbeddingProvider(),
            versions=IngestionVersions(parser=2),
        )
        try:
            yield database, sessions, runtime, service, claim, version, store
        finally:
            await engine.dispose()


@asynccontextmanager
async def _blocked_chunk_write(database, operation):
    """Delay a real write while letting the independent job heartbeat execute."""
    assert operation in {"INSERT", "UPDATE OF embedding"}
    key = int(uuid4().int % (2**30))
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION delay_chunk_write() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN PERFORM pg_advisory_xact_lock({key}); RETURN NULL; END $$"
            )
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER delay_chunk_write BEFORE {operation} ON document_chunks "
                "FOR EACH STATEMENT EXECUTE FUNCTION delay_chunk_write()"
            )
        )
    async with database.engine.connect() as blocker:
        await blocker.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        try:
            yield key
        finally:
            await blocker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


async def _wait_until_blocked(database, key, work):
    async with asyncio.timeout(15):
        while True:
            if work.done():
                await work
                pytest.fail("ingestion finished without reaching the delayed write")
            async with database.sessions() as session:
                waiting = await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND classid=0 AND objid=:key AND NOT granted"
                    ),
                    {"key": key},
                )
            if waiting:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE OF embedding"])
async def test_heartbeat_renews_lease_while_ingestion_write_is_blocked(operation):
    async with _ingestion_fixture() as (database, sessions, runtime, service, claim, version, _):
        work = None
        try:
            async with _blocked_chunk_write(database, operation) as key:
                work = asyncio.create_task(service(claim))
                await _wait_until_blocked(database, key, work)
                # Must complete while the chunk transaction is still held open.
                assert await asyncio.wait_for(runtime.heartbeat(claim), timeout=1) is False
                async with database.sessions() as session:
                    job = await session.get(Job, claim.job_id)
                    assert job.heartbeat_at > job.started_at
                    assert job.lease_expires_at > datetime.now(UTC) + timedelta(seconds=50)
                assert not work.done()
            await asyncio.wait_for(work, timeout=15)
            assert await runtime.succeed(claim) == "succeeded"
            async with sessions() as session:
                document = await session.get(DocumentVersion, version)
                generation = await session.scalar(select(DocumentIngestionGeneration))
                assert document.status == "ready"
                assert generation.active and generation.stage == "ready"
                count = await session.scalar(select(func.count()).select_from(DocumentChunk))
                assert count == generation.chunk_count == generation.embedded_count > 1
        finally:
            if work is not None:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE OF embedding"])
@pytest.mark.parametrize("loss", ["expired", "reclaimed", "cancelled"])
async def test_lost_lease_rolls_back_checkpoint_and_new_claim_can_recover(operation, loss):
    async with _ingestion_fixture() as (
        database,
        sessions,
        runtime,
        service,
        claim,
        version,
        store,
    ):
        work = None
        resumed = None
        try:
            async with _blocked_chunk_write(database, operation) as key:
                work = asyncio.create_task(service(claim))
                await _wait_until_blocked(database, key, work)
                if loss == "cancelled":
                    await asyncio.wait_for(
                        runtime.cancel(job_id=claim.job_id, tenant_id=claim.tenant_id), 1
                    )
                else:
                    async with database.sessions.begin() as session:
                        await session.execute(
                            update(Job)
                            .where(Job.id == claim.job_id)
                            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                        )
                    if loss == "reclaimed":
                        resumed = await runtime.claim(job_id=claim.job_id, worker_id="new-owner")
                        assert resumed.fencing_token > claim.fencing_token
            with pytest.raises(DocumentIngestionError) as error:
                await asyncio.wait_for(work, 15)
            assert error.value.code == "document_execution_stale"
            async with sessions() as session:
                document = await session.get(DocumentVersion, version)
                generation = await session.scalar(select(DocumentIngestionGeneration))
                chunks = (await session.scalars(select(DocumentChunk))).all()
                assert document.status != "ready" and not generation.active
                assert all(row.embedding is None for row in chunks)
                if operation == "INSERT":
                    assert chunks == [] and generation.chunk_count == 0
                    assert document.content_sha256_verified_at is None
                    assert (
                        await session.scalar(select(func.count()).select_from(DocumentStructure))
                        == 0
                    )
                else:
                    assert len(chunks) == generation.chunk_count > 1
                    assert generation.stage == "embed" and generation.embedded_count == 0
            if loss != "cancelled":
                resumed = resumed or await runtime.claim(job_id=claim.job_id, worker_id="new-owner")
                assert resumed is not None
                await service(resumed)
                await runtime.succeed(resumed)
                async with sessions() as session:
                    generation = await session.scalar(select(DocumentIngestionGeneration))
                    assert generation.active and generation.stage == "ready"
                    assert (
                        await session.scalar(select(func.count()).select_from(DocumentStructure))
                        == 1
                    )
                # A valid EMBED checkpoint survives rollback; it is not downloaded again.
                assert store.head_calls == (2 if operation == "INSERT" else 1)
        finally:
            if work is not None:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


async def test_chunk_writes_are_batched_and_preserve_search_and_structure():
    async with _ingestion_fixture(repetitions=205) as (_, sessions, runtime, service, claim, _, _):
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO document_chunks"):
                statements.append(statement)

        engine = sessions.kw["bind"]
        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        try:
            await service(claim)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
        await runtime.succeed(claim)
        async with sessions() as session:
            chunks = (
                await session.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index))
            ).all()
            count = len(chunks)
            assert count >= 205
            assert 1 < len(statements) <= (count + 99) // 100
            assert [row.chunk_index for row in chunks] == list(range(count))
            assert len({row.id for row in chunks}) == count
            assert all(row.embedding is not None and "price" in row.search_vector for row in chunks)
            assert await session.scalar(select(func.count()).select_from(DocumentStructure)) == 1


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE OF embedding"])
async def test_final_fence_does_not_deadlock_with_job_first_contender(operation):
    async with _ingestion_fixture() as (database, sessions, _, service, claim, _, _):
        job_locked = asyncio.Event()

        async def contend():
            async with database.sessions.begin() as session:
                await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
                job_locked.set()
                await session.scalar(select(DocumentIngestionGeneration).with_for_update())

        work = contender = None
        try:
            async with _blocked_chunk_write(database, operation) as key:
                work = asyncio.create_task(service(claim))
                await _wait_until_blocked(database, key, work)
                contender = asyncio.create_task(contend())
                await asyncio.wait_for(job_locked.wait(), 2)
            # This completes only if ingestion releases version/generation without
            # waiting in the opposite direction for the contender's Job lock.
            await asyncio.wait_for(contender, 2)
            with pytest.raises(DocumentIngestionError) as error:
                await asyncio.wait_for(work, 3)
            assert error.value.code == "document_processing_busy"
            assert error.value.retryable
            async with sessions() as session:
                chunks = (await session.scalars(select(DocumentChunk))).all()
                assert all(row.embedding is None for row in chunks)
                assert not (await session.scalar(select(DocumentIngestionGeneration))).active
                if operation == "INSERT":
                    assert not chunks
            await service(claim)
            async with sessions() as session:
                assert (await session.scalar(select(DocumentIngestionGeneration))).active
        finally:
            for task in (work, contender):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
