from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from tests.demo.test_demo_cleanup_integration import completed_upload
from tests.multipart.test_upload_cleanup_integration import CleanupObjectStore

from enterprise_doc_core.demo.cleanup import DemoCleanupService
from enterprise_doc_core.demo.service import DemoService
from enterprise_doc_core.demo.settings import DemoSettings
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.uploads.models import UploadSession

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("race", [False, True])
async def test_demo_single_put_retains_barrier_and_retries_before_deleting_rows(demo_db, race):
    now = datetime.now(UTC)
    service = DemoService(
        session_factory=demo_db.sessions, settings=DemoSettings(enabled=True), clock=lambda: now
    )
    guest = await service.start()

    class RetirementStore(CleanupObjectStore):
        def __init__(self):
            super().__init__()
            self.retired = []
            self.fail = race

        async def retire_upload_object(self, *, bucket, key, metadata):
            if self.fail:
                return False
            assert metadata == self.object_metadata[key]
            self.retired.append(key)
            return True

    store = RetirementStore()
    upload = await completed_upload(demo_db, guest, store)
    async with demo_db.sessions.begin() as db:
        await db.execute(
            update(UploadSession)
            .where(UploadSession.id == upload.session_id)
            .values(transport="single_put", object_store_upload_id=None)
        )
    now += timedelta(hours=4)
    cleaner = DemoCleanupService(
        session_factory=demo_db.sessions,
        object_store=store,
        documents_bucket="documents",
        clock=lambda: now,
    )
    if race:
        assert await cleaner.run_once() == 0
        async with demo_db.sessions() as db:
            assert await db.get(UploadSession, upload.session_id) is not None
        store.fail = False
    assert await cleaner.run_once() == 1
    assert store.retired == [upload.object_key] and not store.delete_calls
    async with demo_db.sessions() as db:
        assert await db.get(Tenant, guest.snapshot.tenant_id) is None
    assert await cleaner.run_once() == 0
