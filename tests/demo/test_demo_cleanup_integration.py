from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.browser_sessions.conftest import BrowserDatabase
from tests.multipart.test_upload_cleanup_integration import CleanupObjectStore, SeededSession

from enterprise_doc_core.demo.cleanup import DemoCleanupService
from enterprise_doc_core.demo.models import DemoDay, DemoWorkspace
from enterprise_doc_core.demo.service import DemoService, IssuedDemoSession
from enterprise_doc_core.demo.settings import DemoSettings
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity.models import Tenant, User
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.policy import build_object_key

pytestmark = pytest.mark.integration


async def completed_upload(
    db: BrowserDatabase, guest: IssuedDemoSession, store: CleanupObjectStore
) -> SeededSession:
    upload, document, version = uuid4(), uuid4(), uuid4()
    seeded = SeededSession(
        guest.snapshot.tenant_id,
        guest.snapshot.actor_id,
        uuid4(),
        upload,
        document,
        version,
        build_object_key(session_id=upload, version_id=version),
        "demo-upload",
        20,
        b"public demo document",
        (),
    )
    store.register_session(seeded, multipart_exists=False, object_exists=True)
    async with db.sessions.begin() as session:
        source = UploadSession(
            id=upload,
            tenant_id=seeded.tenant_id,
            actor_id=seeded.actor_id,
            pending_document_id=document,
            pending_version_id=version,
            status="completed",
            idempotency_key=uuid4().hex,
            request_fingerprint="a" * 64,
            object_key=seeded.object_key,
            object_store_upload_id=seeded.upload_id,
            original_filename="demo.txt",
            extension="txt",
            declared_media_type="text/plain",
            size_bytes=20,
            declared_sha256="a" * 64,
            part_size_bytes=20,
            expected_part_count=1,
            reserved_bytes=0,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(source)
        session.add(
            Document(
                id=document,
                tenant_id=seeded.tenant_id,
                created_by=seeded.actor_id,
                title="demo.txt",
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version,
                tenant_id=seeded.tenant_id,
                document_id=document,
                upload_session_id=upload,
                version_number=1,
                status="uploaded",
                object_key=seeded.object_key,
                original_filename="demo.txt",
                declared_media_type="text/plain",
                detected_media_type="text/plain",
                size_bytes=20,
                declared_sha256="a" * 64,
                created_by=seeded.actor_id,
            )
        )
        await session.flush()
        source.document_version_id = version
    return seeded


async def test_cleanup_handles_real_fk_cycle_preserves_other_tenant_and_day_budget(
    demo_db: BrowserDatabase,
) -> None:
    now = datetime.now(UTC)
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True), clock=lambda: now
    )
    guest = await service.start()
    store = CleanupObjectStore()
    upload = await completed_upload(demo_db, guest, store)
    other_id = uuid4()
    async with demo_db.sessions.begin() as session:
        session.add(
            Tenant(id=other_id, slug="permanent", name="Existing enterprise", quota_bytes=1024)
        )
    cleaner = DemoCleanupService(
        session_factory=demo_db.sessions,
        object_store=store,
        documents_bucket="documents",
        clock=lambda: now,
    )
    now += timedelta(hours=2, minutes=1)
    assert await cleaner.run_once() == 0  # signed URL expiry grace is mandatory
    assert not store.delete_calls
    async with demo_db.sessions() as session:
        tenant = await session.get(Tenant, guest.snapshot.tenant_id)
        assert tenant is not None and not tenant.is_active
    now += timedelta(hours=1)
    assert await cleaner.run_once() == 1
    assert store.delete_calls == [upload.object_key]
    assert await cleaner.run_once() == 0
    async with demo_db.sessions() as session:
        assert await session.get(Tenant, guest.snapshot.tenant_id) is None
        assert await session.get(User, guest.snapshot.actor_id) is None
        assert await session.get(Tenant, other_id) is not None
        assert await session.scalar(select(DemoDay.workspaces_created)) == 1
        receipt = await session.get(DemoWorkspace, guest.snapshot.session_id)
        assert receipt is not None and receipt.cleaned_at == now
        assert receipt.tenant_id is None and receipt.credential_digest is None


async def test_cleanup_retries_object_failure_without_losing_ownership_records(
    demo_db: BrowserDatabase,
) -> None:
    now = datetime.now(UTC)
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True), clock=lambda: now
    )
    guest = await service.start()
    store = CleanupObjectStore()
    upload = await completed_upload(demo_db, guest, store)
    store.delete_errors[upload.object_key] = ObjectStoreUnavailable()
    cleaner = DemoCleanupService(
        session_factory=demo_db.sessions,
        object_store=store,
        documents_bucket="documents",
        clock=lambda: now,
    )
    now += timedelta(hours=4)
    assert await cleaner.run_once() == 0
    async with demo_db.sessions() as session:
        assert await session.get(UploadSession, upload.session_id) is not None
        tenant = await session.get(Tenant, guest.snapshot.tenant_id)
        assert tenant is not None and not tenant.is_active
    store.delete_errors.clear()
    assert await cleaner.run_once() == 1


async def test_cleanup_refuses_unproven_object_metadata(demo_db: BrowserDatabase) -> None:
    now = datetime.now(UTC)
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True), clock=lambda: now
    )
    guest = await service.start()
    store = CleanupObjectStore()
    upload = await completed_upload(demo_db, guest, store)
    store.object_metadata[upload.object_key]["upload-session-id"] = str(uuid4())
    cleaner = DemoCleanupService(
        session_factory=demo_db.sessions,
        object_store=store,
        documents_bucket="documents",
        clock=lambda: now,
    )
    now += timedelta(hours=4)
    assert await cleaner.run_once() == 0
    assert store.delete_calls == []
