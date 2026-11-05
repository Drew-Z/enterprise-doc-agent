import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import DocumentEnvelopeViolation
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.jobs import Job, OutboxEvent
from enterprise_doc_core.object_store.errors import ObjectStoreRejected, ObjectStoreUnavailable
from enterprise_doc_core.object_store.signed_upload import SignedObjectWrite
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.session_service import (
    UploadCompletionPartsInvalid,
    UploadCompletionVerificationFailed,
    UploadSessionExpired,
    UploadSessionNotFound,
    UploadSessionService,
)
from tests.multipart.test_upload_complete_integration import (
    CompletionObjectStore,
    _cleanup_seeded,
    _seed_upload,
)


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["normal", "concurrent", "lost-response", "receipt-mismatch"])
async def test_signed_content_completion_is_durable_and_recovers_ambiguous_write(mode):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    writes = 0

    class Writer:
        async def write_content(self, *, bucket, key, body, content_type, metadata):
            nonlocal writes
            # A second real connection can acquire the row: no lock spans PUT.
            async with factory.begin() as db:
                query = select(UploadSession).where(UploadSession.id == seeded.session_id)
                if mode != "concurrent":
                    query = query.with_for_update(nowait=True)
                row = await db.scalar(query)
                assert row.status in {"completing", "completed"}
                assert metadata == store.metadata and key == row.object_key
            if store.object_exists:
                raise ObjectStoreRejected()
            writes += 1
            assert body == seeded.content
            store.object_exists = True
            if mode == "lost-response":
                raise ObjectStoreUnavailable()
            digest = "0" * 64 if mode == "receipt-mismatch" else hashlib.sha256(body).hexdigest()
            return SignedObjectWrite(store._head(), digest)

    try:
        async with factory.begin() as db:
            await db.execute(
                update(UploadSession)
                .where(UploadSession.id == seeded.session_id)
                .values(transport="single_put", object_store_upload_id=None)
            )
        service = UploadSessionService(
            session_factory=factory, object_store=store, documents_bucket="documents"
        )
        writer = Writer()

        async def complete():
            return await service.complete_content(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                content=seeded.content,
                writer=writer,
            )

        results = (
            await asyncio.gather(complete(), complete())
            if mode == "concurrent"
            else [await complete()]
        )
        assert {r.version_id for r in results} == {seeded.pending_version_id}
        replay = await complete()
        assert replay.replayed and replay.version_id == seeded.pending_version_id
        assert writes == 1
        if mode == "normal":
            assert store.read_calls == 0
        elif mode in {"lost-response", "receipt-mismatch"}:
            assert store.read_calls == 1
        async with factory() as db:
            row = await db.get(UploadSession, seeded.session_id)
            tenant = await db.get(Tenant, seeded.principal.tenant_id)
            assert row.status == "completed" and row.reserved_bytes == 0
            assert tenant.used_storage_bytes == len(seeded.content)
            assert tenant.reserved_storage_bytes == 0
            assert (
                await db.scalar(
                    select(func.count()).select_from(Job).where(Job.tenant_id == tenant.id)
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.tenant_id == tenant.id)
                )
                == 1
            )
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario", ["sha", "size", "actor", "tenant", "expired", "multipart", "envelope"]
)
async def test_signed_completion_rejects_invalid_input_before_object_write(scenario):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(
        factory, content=b"not-a-pdf" if scenario == "envelope" else b"%PDF-1.7"
    )
    store = CompletionObjectStore(seeded)

    class Writer:
        async def write_content(self, **kwargs):
            pytest.fail("rejected input reached object writer")

    try:
        async with factory.begin() as db:
            values = (
                {}
                if scenario == "multipart"
                else {"transport": "single_put", "object_store_upload_id": None}
            )
            if scenario == "expired":
                values["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
            if values:
                await db.execute(
                    update(UploadSession)
                    .where(UploadSession.id == seeded.session_id)
                    .values(**values)
                )
        principal = seeded.principal.context
        if scenario in {"actor", "tenant"}:
            principal = PrincipalContext(
                tenant_id=str(uuid4()) if scenario == "tenant" else principal.tenant_id,
                actor_id=str(uuid4()) if scenario == "actor" else principal.actor_id,
                role="owner",
            )
        content = (
            b"X" + seeded.content[1:]
            if scenario == "sha"
            else seeded.content + b"x"
            if scenario == "size"
            else seeded.content
        )
        expected = (
            UploadSessionNotFound
            if scenario in {"actor", "tenant"}
            else UploadSessionExpired
            if scenario == "expired"
            else UploadCompletionPartsInvalid
            if scenario == "multipart"
            else DocumentEnvelopeViolation
            if scenario == "envelope"
            else UploadCompletionVerificationFailed
        )
        service = UploadSessionService(
            session_factory=factory, object_store=store, documents_bucket="documents"
        )
        with pytest.raises(expected):
            await service.complete_content(
                principal=principal, session_id=seeded.session_id, content=content, writer=Writer()
            )
        assert store.read_calls == store.delete_calls == 0
        async with factory() as db:
            session = await db.get(UploadSession, seeded.session_id)
            tenant = await db.get(Tenant, seeded.principal.tenant_id)
            assert session.status == ("failed" if scenario == "envelope" else "active")
            assert session.document_version_id is None and tenant.used_storage_bytes == 0
            assert tenant.reserved_storage_bytes == (
                0 if scenario == "envelope" else len(seeded.content)
            )
            assert (
                await db.scalar(
                    select(func.count()).select_from(Job).where(Job.tenant_id == tenant.id)
                )
                == 0
            )
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()
