import asyncio
import base64
import hashlib
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, ReadError
from sqlalchemy import delete, func, select, update

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.jobs import Job, OutboxEvent
from enterprise_doc_core.object_store import ObjectStoreNotFound, ObjectStoreUnavailable
from enterprise_doc_core.object_store.errors import ObjectStoreRejected
from enterprise_doc_core.object_store.models import ObjectContent, ObjectHead
from enterprise_doc_core.object_store.signed_upload import SignedObjectWrite
from enterprise_doc_core.uploads import UploadCreationService, UploadSession, UploadSessionService
from enterprise_doc_core.uploads.service import CreateUploadSessionInput
from tests.multipart.test_upload_create_integration import FixturePrincipal, TokenPrincipalResolver


class ContentStore:
    """External object storage boundary; all HTTP/domain/database code is real."""

    def __init__(self, mode):
        self.mode = mode
        self.objects = {}
        self.writes = 0
        self.reads = 0
        self.failed = False

    async def create_upload(self, **kwargs):
        return "legacy-multipart-id"

    async def write_content(self, *, bucket, key, body, content_type, metadata):
        if self.mode == "before-write" and not self.failed:
            self.failed = True
            raise ObjectStoreUnavailable()
        if key in self.objects:
            raise ObjectStoreRejected()
        head = ObjectHead(len(body), '"stored"', None, content_type, dict(metadata))
        self.objects[key] = ObjectContent(head, body)
        self.writes += 1
        if self.mode == "write-response" and not self.failed:
            self.failed = True
            raise ObjectStoreUnavailable()
        return SignedObjectWrite(head, hashlib.sha256(body).hexdigest())

    async def read_object(self, *, bucket, key, max_bytes):
        self.reads += 1
        if key not in self.objects:
            raise ObjectStoreNotFound()
        assert len(self.objects[key].content) <= max_bytes
        return self.objects[key]


class LoseFirstResponse(ASGITransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        if request.url.path.endswith("/content") and response.status_code == 201:
            await response.aread()
            raise ReadError("response lost after durable server completion", request=request)
        return response


@pytest.mark.integration
@pytest.mark.parametrize(
    "mode",
    ["normal", "concurrent", "before-write", "write-response", "http-response", "max", "legacy"],
)
async def test_content_http_same_key_recovery_finalizes_exactly_once(mode):
    settings = ApiSettings(_env_file=None, upload={"single_put_enabled": True})
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    principal = FixturePrincipal(uuid4(), uuid4(), uuid4(), "owner", 2 * 1024**2)
    store = ContentStore(mode)
    body = b"x" * 1048576 if mode == "max" else b"hello from bounded HTTP"
    payload = {
        "filename": "note.txt",
        "mediaType": "text/plain",
        "sizeBytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "contentBase64": base64.b64encode(body).decode("ascii"),
    }
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
            settings=settings.upload,
            object_store=store,
            documents_bucket="documents",
        ),
        signed_upload_writer=store,
    )
    try:
        async with factory.begin() as db:
            db.add(
                Tenant(
                    id=principal.tenant_id,
                    name="HTTP upload",
                    slug=str(principal.tenant_id),
                    quota_bytes=principal.quota_bytes,
                )
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
        if mode == "legacy":
            old_service = UploadCreationService(
                session_factory=factory,
                settings=settings.upload.model_copy(update={"single_put_enabled": False}),
                object_store=store,
                documents_bucket="documents",
            )
            original = await old_service.create(
                principal=principal.context,
                idempotency_key="same-persisted-intent",
                request=CreateUploadSessionInput(
                    filename="note.txt",
                    media_type="text/plain",
                    size_bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    transport="single_put",
                ),
            )
        transport = (
            LoseFirstResponse(app=app) if mode == "http-response" else ASGITransport(app=app)
        )
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:

                async def upload():
                    return await client.post(
                        "/api/upload-sessions/content",
                        json=payload,
                        headers={
                            "Authorization": "Bearer owner",
                            "Idempotency-Key": "same-persisted-intent",
                        },
                    )

                if mode == "legacy":
                    response = await upload()
                    assert response.status_code == 200
                    assert response.json()["completion"] is None
                    assert response.json()["session"]["sessionId"] == str(original.session_id)
                    assert store.writes == store.reads == 0
                    async with factory() as db:
                        tenant = await db.get(Tenant, principal.tenant_id)
                        assert tenant.reserved_storage_bytes == len(body)
                        assert tenant.used_storage_bytes == 0
                        assert (
                            await db.scalar(
                                select(func.count())
                                .select_from(Job)
                                .where(Job.tenant_id == principal.tenant_id)
                            )
                            == 0
                        )
                    return
                if mode == "http-response":
                    with pytest.raises(ReadError):
                        await upload()
                elif mode == "before-write":
                    first = await upload()
                    assert first.status_code == 409
                    async with factory() as db:
                        row = await db.scalar(
                            select(UploadSession).where(
                                UploadSession.tenant_id == principal.tenant_id
                            )
                        )
                        tenant = await db.get(Tenant, principal.tenant_id)
                        assert row.status == "completing" and row.reserved_bytes == len(body)
                        assert (
                            tenant.reserved_storage_bytes == len(body)
                            and tenant.used_storage_bytes == 0
                        )
                elif mode == "concurrent":
                    responses = await asyncio.gather(upload(), upload())
                    assert all(response.status_code in {200, 201} for response in responses)
                    assert (
                        len({response.json()["completion"]["versionId"] for response in responses})
                        == 1
                    )
                else:
                    assert (await upload()).status_code == 201
                final = await upload()
                assert final.status_code == 200
                result = final.json()
                assert result["session"]["replayed"]
                assert result["completion"]["status"] == "completed"
                assert result["completion"]["sessionId"] == result["session"]["sessionId"]
                assert final.headers["cache-control"] == "no-store"
                assert (await upload()).json()["completion"]["versionId"] == result["completion"][
                    "versionId"
                ]
        assert store.writes == 1
        if mode == "normal":
            assert store.reads == 0
        elif mode in {"before-write", "write-response"}:
            assert store.reads == 1
        async with factory() as db:
            for model in (UploadSession, Document, DocumentVersion, Job, OutboxEvent):
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.tenant_id == principal.tenant_id)
                    )
                    == 1
                )
            tenant = await db.get(Tenant, principal.tenant_id)
            assert tenant.used_storage_bytes == len(body) and tenant.reserved_storage_bytes == 0
    finally:
        async with factory.begin() as db:
            await db.execute(
                update(UploadSession)
                .where(UploadSession.tenant_id == principal.tenant_id)
                .values(document_version_id=None)
            )
            for model in (DocumentVersion, Document, UploadSession, Membership):
                await db.execute(delete(model).where(model.tenant_id == principal.tenant_id))
            await db.execute(delete(User).where(User.id == principal.actor_id))
            await db.execute(delete(Tenant).where(Tenant.id == principal.tenant_id))
        await engine.dispose()
