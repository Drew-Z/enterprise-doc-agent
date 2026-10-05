from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.object_store.models import PresignedObjectUpload
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.service import UploadCreationService
from enterprise_doc_core.uploads.session_service import UploadSessionService
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.multipart.test_upload_create_integration import FixturePrincipal, TokenPrincipalResolver

pytestmark = pytest.mark.integration


class SigningStore:
    async def presign_object_put(self, **kwargs):
        return PresignedObjectUpload("https://objects.example/signed", {}, 60)


async def test_inline_signature_and_replay_preserve_one_reservation_and_durable_expiry(browser_db):
    principal = FixturePrincipal(uuid4(), uuid4(), uuid4(), "upload-owner", 100)
    factory = browser_db.sessions
    async with factory.begin() as db:
        db.add(
            Tenant(id=principal.tenant_id, name="Inline test", slug=str(uuid4()), quota_bytes=100)
        )
        db.add(User(id=principal.actor_id, email=f"{principal.actor_id}@example.test"))
        await db.flush()
        db.add(
            Membership(
                id=principal.membership_id,
                tenant_id=principal.tenant_id,
                user_id=principal.actor_id,
                role="owner",
            )
        )
    settings = ApiSettings(_env_file=None, upload=UploadSettings(single_put_enabled=True))
    store = SigningStore()
    app = create_app(
        settings=settings,
        checkers=[],
        principal_resolver=TokenPrincipalResolver([principal]),
        upload_creation_service=UploadCreationService(
            session_factory=factory,
            settings=settings.upload,
            object_store=store,
            documents_bucket="documents",
        ),
        upload_session_service=UploadSessionService(
            session_factory=factory,
            object_store=store,
            documents_bucket="documents",
        ),
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        results = []
        for _ in range(2):
            results.append(
                await client.post(
                    "/api/upload-sessions?includeSignature=true",
                    headers={
                        "Authorization": "Bearer upload-owner",
                        "Idempotency-Key": "same",
                    },
                    json={
                        "filename": "small.txt",
                        "sizeBytes": 25,
                        "mediaType": "text/plain",
                        "sha256": "a" * 64,
                        "transport": "single_put",
                    },
                )
            )
    assert [result.status_code for result in results] == [201, 200]
    assert results[0].json()["sessionId"] == results[1].json()["sessionId"]
    assert all(result.json()["initialUpload"]["expiresInSeconds"] == 60 for result in results)
    async with factory() as db:
        tenant = await db.get(Tenant, principal.tenant_id)
        rows = (await db.scalars(select(UploadSession))).all()
        assert tenant.reserved_storage_bytes == 25 and tenant.used_storage_bytes == 0
        assert len(rows) == 1 and rows[0].object_store_upload_id is None
        assert rows[0].signed_put_expires_at > datetime.now(UTC)
        assert rows[0].signed_put_expires_at <= rows[0].expires_at
