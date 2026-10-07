import asyncio
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.jobs.models import Job, JobEvent, OutboxEvent
from enterprise_doc_core.uploads import UploadCreationService, UploadSession, UploadSessionService
from enterprise_doc_core.uploads.service import CreateUploadSessionInput
from enterprise_doc_core.uploads.session_service import UploadCompletionFailed
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.multipart.test_upload_content_http_integration import ContentStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def completion_case(browser_db):
    tenant_id, actor_id = uuid4(), uuid4()
    async with browser_db.sessions.begin() as db:
        db.add(Tenant(id=tenant_id, name="Batch upload", slug=str(tenant_id), quota_bytes=10000))
        db.add(User(id=actor_id, email=f"{actor_id}@example.test"))
        await db.flush()
        db.add(Membership(tenant_id=tenant_id, user_id=actor_id, role="owner"))
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
    settings = UploadSettings(single_put_enabled=True)
    store = ContentStore("normal")
    creation = UploadCreationService(
        session_factory=browser_db.sessions,
        settings=settings,
        object_store=store,
        documents_bucket="documents",
    )
    service = UploadSessionService(
        session_factory=browser_db.sessions,
        settings=settings,
        object_store=store,
        documents_bucket="documents",
        ingestion_max_attempts=5,
    )
    body = b"Synthetic atomic upload completion."
    created = await creation.create(
        principal=principal,
        idempotency_key="batch-completion",
        request=CreateUploadSessionInput(
            filename="batch.txt",
            size_bytes=len(body),
            media_type="text/plain",
            sha256=hashlib.sha256(body).hexdigest(),
            transport="single_put",
        ),
    )
    return browser_db, principal, store, service, body, created


async def test_new_content_completion_uses_six_sql_round_trips_and_persists_full_graph(
    completion_case,
):
    db, principal, store, service, body, created = completion_case
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", observed)
    try:
        result = await service.complete_content(
            principal=principal, session_id=created.session_id, content=body, writer=store
        )
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", observed)
    assert len(statements) <= 6, [s.split()[0] for s in statements]
    assert not result.replayed and result.session_id == created.session_id
    assert store.writes == 1 and store.reads == 0
    async with db.sessions() as session:
        for model in (Document, DocumentVersion, Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        upload = await session.get(UploadSession, created.session_id)
        version = await session.get(DocumentVersion, result.version_id)
        tenant = await session.get(Tenant, upload.tenant_id)
        job = await session.scalar(select(Job))
        assert upload.status == "completed" and upload.reserved_bytes == 0
        assert upload.completed_at == result.completed_at
        assert upload.document_version_id == version.id == upload.pending_version_id
        assert version.document_id == result.document_id == upload.pending_document_id
        assert version.size_bytes == len(body) and version.content_sha256_verified_at
        assert version.declared_sha256 == hashlib.sha256(body).hexdigest()
        assert tenant.reserved_storage_bytes == 0 and tenant.used_storage_bytes == len(body)
        assert job.document_version_id == version.id and job.max_attempts == 5
        assert job.payload == {"document_version_id": str(version.id)}


@pytest.mark.parametrize(
    "failed_table",
    [
        "documents",
        "document_versions",
        "jobs",
        "job_events",
        "audit_events",
        "outbox_events",
        "tenants",
        "upload_sessions",
    ],
)
async def test_completion_write_failure_preserves_reservation_and_recovers_same_object(
    completion_case, failed_table
):
    db, principal, store, service, body, created = completion_case
    condition = {
        "tenants": "used_storage_bytes = 0",
        "upload_sessions": "status <> 'completed'",
    }.get(failed_table, "false")
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                f'ALTER TABLE "{failed_table}" ADD CONSTRAINT reject_completion_write '
                f"CHECK ({condition}) NOT VALID"
            )
        )
    with pytest.raises(UploadCompletionFailed):
        await service.complete_content(
            principal=principal, session_id=created.session_id, content=body, writer=store
        )
    async with db.sessions() as session:
        for model in (Document, DocumentVersion, Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        upload = await session.get(UploadSession, created.session_id)
        tenant = await session.get(Tenant, upload.tenant_id)
        assert upload.status == "completing" and upload.document_version_id is None
        assert upload.reserved_bytes == tenant.reserved_storage_bytes == len(body)
        assert tenant.used_storage_bytes == 0
    async with db.engine.begin() as connection:
        await connection.execute(
            text(f'ALTER TABLE "{failed_table}" DROP CONSTRAINT reject_completion_write')
        )
    recovered = await service.complete_content(
        principal=principal, session_id=created.session_id, content=body, writer=store
    )
    assert recovered.session_id == created.session_id
    assert store.writes == 1 and store.reads == 1
    async with db.sessions() as session:
        for model in (Document, DocumentVersion, Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        upload = await session.get(UploadSession, created.session_id)
        tenant = await session.get(Tenant, upload.tenant_id)
        assert upload.reserved_bytes == tenant.reserved_storage_bytes == 0
        assert tenant.used_storage_bytes == len(body)


async def test_four_concurrent_completions_share_one_graph_and_storage_conversion(completion_case):
    db, principal, store, service, body, created = completion_case
    async with asyncio.timeout(15):
        results = await asyncio.gather(
            *(
                service.complete_content(
                    principal=principal, session_id=created.session_id, content=body, writer=store
                )
                for _ in range(4)
            )
        )
    assert len({(r.document_id, r.version_id, r.completed_at) for r in results}) == 1
    assert store.writes == 1
    async with db.sessions() as session:
        for model in (Document, DocumentVersion, Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        upload = await session.get(UploadSession, created.session_id)
        tenant = await session.get(Tenant, upload.tenant_id)
        assert upload.reserved_bytes == tenant.reserved_storage_bytes == 0
        assert tenant.used_storage_bytes == len(body)


async def test_final_commit_acknowledgment_loss_reads_original_durable_receipt(
    completion_case, monkeypatch
):
    db, principal, store, service, body, created = completion_case
    original = db.engine.sync_engine.dialect.do_commit
    commits = 0

    def commit_then_lose_acknowledgment(connection):
        nonlocal commits
        original(connection)
        commits += 1
        # The first transaction claims completion. The second persists its graph.
        if commits == 2:
            raise OSError("synthetic database acknowledgment loss after commit")

    monkeypatch.setattr(db.engine.sync_engine.dialect, "do_commit", commit_then_lose_acknowledgment)
    result = await service.complete_content(
        principal=principal, session_id=created.session_id, content=body, writer=store
    )
    assert commits >= 2 and not result.replayed
    replay = await service.complete_content(
        principal=principal, session_id=created.session_id, content=body, writer=store
    )
    assert replay.replayed and result.version_id == replay.version_id
    assert result.completed_at == replay.completed_at and store.writes == 1
    async with db.sessions() as session:
        for model in (Document, DocumentVersion, Job, JobEvent, AuditEvent, OutboxEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        upload = await session.get(UploadSession, created.session_id)
        tenant = await session.get(Tenant, upload.tenant_id)
        assert upload.reserved_bytes == tenant.reserved_storage_bytes == 0
        assert tenant.used_storage_bytes == len(body)
