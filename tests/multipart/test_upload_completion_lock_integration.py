import asyncio

import pytest
from sqlalchemy import func, select, update

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.documents import Document, DocumentVersion
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.jobs import Job, OutboxEvent
from enterprise_doc_core.uploads import (
    CompleteUploadSessionInput,
    UploadSession,
    UploadSessionService,
)
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.multipart.test_upload_complete_integration import (
    CompletionObjectStore,
    _completion_request,
    _seed_upload,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("transport", ["multipart", "single_put"])
async def test_completion_and_replay_do_not_wait_for_unrelated_audit_insert(browser_db, transport):
    sessions = browser_db.sessions
    seeded = await _seed_upload(sessions, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    request = _completion_request(seeded.parts)
    if transport == "single_put":
        store.object_exists = True
        request = CompleteUploadSessionInput(parts=())
        async with sessions.begin() as db:
            await db.execute(
                update(UploadSession)
                .where(UploadSession.id == seeded.session_id)
                .values(
                    transport="single_put",
                    object_store_upload_id=None,
                    expected_part_count=1,
                    part_size_bytes=len(seeded.content),
                )
            )
    service = UploadSessionService(
        session_factory=sessions, object_store=store, documents_bucket="documents"
    )

    async def complete():
        return await service.complete(
            principal=seeded.principal.context, session_id=seeded.session_id, request=request
        )

    async with sessions.begin() as writer:
        await append_audit_event(
            writer,
            tenant_id=seeded.principal.tenant_id,
            actor_id=seeded.principal.actor_id,
            action="integration.unrelated",
            resource_type="integration",
        )
        # A real FK insert holds KEY SHARE until this outer transaction finishes.
        # Completion must still serialize competing quota updates, but not this insert.
        async with asyncio.timeout(2):
            results = await asyncio.gather(complete(), complete())
            replay = await complete()
        assert {r.version_id for r in results} == {seeded.pending_version_id}
        assert replay.replayed

    async with sessions() as db:
        for model in (Document, DocumentVersion, Job, OutboxEvent):
            assert await db.scalar(select(func.count()).select_from(model)) == 1
        tenant = await db.get(Tenant, seeded.principal.tenant_id)
        upload = await db.get(UploadSession, seeded.session_id)
        assert tenant.used_storage_bytes == len(seeded.content)
        assert tenant.reserved_storage_bytes == 0
        assert upload.reserved_bytes == 0 and upload.status == "completed"
