import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import DocumentVersion
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.object_store.errors import ObjectStoreNotFound
from enterprise_doc_core.object_store.models import PresignedObjectUpload
from enterprise_doc_core.uploads.cleanup import UploadCleanupService
from enterprise_doc_core.uploads.models import UploadSession, UploadTransport
from enterprise_doc_core.uploads.service import (
    CreateUploadSessionInput,
    UploadCreationService,
    UploadIdempotencyConflict,
)
from enterprise_doc_core.uploads.session_service import (
    CompleteUploadSessionInput,
    UploadAbortFailed,
    UploadCompletionVerificationFailed,
    UploadSessionNotFound,
    UploadSessionService,
)
from tests.multipart.test_upload_complete_integration import (
    CompletionObjectStore,
    CrashBeforeFinalizationService,
    _cleanup_seeded,
    _seed_upload,
)


class DirectStore:
    async def create_upload(self, **kwargs):
        raise AssertionError("single PUT must not create multipart state")

    async def presign_object_put(self, **kwargs):
        return PresignedObjectUpload("https://example.test/signed", {}, 60)


@pytest.mark.integration
async def test_single_put_concurrent_creation_and_durable_presign():
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    tenant_id, actor_id = uuid4(), uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
    store = DirectStore()
    creation = UploadCreationService(
        session_factory=factory,
        settings=settings.upload,
        object_store=store,
        documents_bucket="documents",
    )
    sessions = UploadSessionService(
        session_factory=factory, object_store=store, documents_bucket="documents"
    )
    request = CreateUploadSessionInput(
        "small.txt", 25, "text/plain", "a" * 64, UploadTransport.SINGLE_PUT
    )
    try:
        async with factory.begin() as db:
            db.add(
                Tenant(id=tenant_id, name="Single PUT test", slug=str(tenant_id), quota_bytes=100)
            )
            db.add(User(id=actor_id, email=f"{actor_id}@example.test"))
            await db.flush()
            db.add(Membership(id=uuid4(), tenant_id=tenant_id, user_id=actor_id, role="owner"))
        results = await asyncio.gather(
            *[
                creation.create(principal=principal, idempotency_key="same", request=request)
                for _ in range(4)
            ]
        )
        assert len({r.session_id for r in results}) == 1
        assert sum(not r.replayed for r in results) == 1
        assert all(r.status == "active" and r.transport == "single_put" for r in results)
        settings.upload.single_put_enabled = False
        replay = await creation.create(principal=principal, idempotency_key="same", request=request)
        assert replay.session_id == results[0].session_id and replay.transport == "single_put"
        assert replay.replayed
        with pytest.raises(UploadIdempotencyConflict):
            await creation.create(
                principal=principal,
                idempotency_key="same",
                request=CreateUploadSessionInput("small.txt", 25, "text/plain", "a" * 64),
            )
        signed = await sessions.presign_single_put(
            principal=principal, session_id=results[0].session_id
        )
        assert signed.expires_in_seconds == 60
        async with factory() as db:
            tenant = await db.get(Tenant, tenant_id)
            rows = (
                await db.scalars(select(UploadSession).where(UploadSession.tenant_id == tenant_id))
            ).all()
            assert tenant.reserved_storage_bytes == 25 and tenant.used_storage_bytes == 0
            assert len(rows) == 1 and rows[0].object_store_upload_id is None
            assert rows[0].signed_put_expires_at is not None

        class FallbackStore(DirectStore):
            calls = 0

            async def create_upload(self, **kwargs):
                self.calls += 1
                return "fallback-multipart-id"

        fallback_store = FallbackStore()
        fallback_creation = UploadCreationService(
            session_factory=factory,
            settings=settings.upload,
            object_store=fallback_store,
            documents_bucket="documents",
        )
        fallback = await fallback_creation.create(
            principal=principal, idempotency_key="disabled-new", request=request
        )
        assert fallback.status == "active" and fallback.transport == "multipart"
        assert fallback_store.calls == 1
        settings.upload.single_put_enabled = True
        fallback_replay = await fallback_creation.create(
            principal=principal, idempotency_key="disabled-new", request=request
        )
        assert fallback_replay.session_id == fallback.session_id
        assert fallback_replay.transport == "multipart" and fallback_replay.replayed
        assert fallback_store.calls == 1
        with pytest.raises(UploadSessionNotFound):
            await sessions.presign_single_put(
                principal=PrincipalContext(
                    tenant_id=str(uuid4()), actor_id=str(actor_id), role="owner"
                ),
                session_id=results[0].session_id,
            )
    finally:
        async with factory.begin() as db:
            await db.execute(delete(UploadSession).where(UploadSession.tenant_id == tenant_id))
            await db.execute(delete(Membership).where(Membership.tenant_id == tenant_id))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["missing", "uploaded", "cancel_during_read"])
async def test_single_put_resume_observes_bytes_and_current_state(scenario):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(factory, content=b"%PDF-1.7")

    class ResumeStore(CompletionObjectStore):
        async def read_object(self, **kwargs):
            if scenario == "missing":
                raise ObjectStoreNotFound()
            result = await super().read_object(**kwargs)
            if scenario == "cancel_during_read":
                async with factory.begin() as db:
                    await db.execute(
                        update(UploadSession)
                        .where(UploadSession.id == seeded.session_id)
                        .values(status="aborted", reserved_bytes=0)
                    )
                    await db.execute(
                        update(Tenant)
                        .where(Tenant.id == seeded.principal.tenant_id)
                        .values(reserved_storage_bytes=0)
                    )
            return result

    store = ResumeStore(seeded)
    store.object_exists = True
    try:
        async with factory.begin() as db:
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
            session_factory=factory, object_store=store, documents_bucket="documents"
        )
        result = await service.get(principal=seeded.principal.context, session_id=seeded.session_id)
        assert result.transport == "single_put"
        assert result.status == ("aborted" if scenario == "cancel_during_read" else "active")
        assert len(result.uploaded_parts) == (1 if scenario == "uploaded" else 0)
        if result.uploaded_parts:
            assert result.uploaded_parts[0].size_bytes == len(seeded.content)
        assert store.list_calls == 0 and store.complete_calls == 0
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["concurrent", "crash", "wrong_hash", "foreign_metadata"])
async def test_single_put_completion_verifies_and_finalizes_once(scenario):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    store.object_exists = True
    try:
        async with factory.begin() as db:
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
        if scenario == "wrong_hash":
            store.readback_content = b"%PDF-1.8"
        if scenario == "foreign_metadata":
            store.metadata["version-id"] = str(uuid4())
        cls = CrashBeforeFinalizationService if scenario == "crash" else UploadSessionService
        service = cls(session_factory=factory, object_store=store, documents_bucket="documents")

        async def complete():
            return await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=CompleteUploadSessionInput(parts=()),
            )

        if scenario in {"wrong_hash", "foreign_metadata"}:
            with pytest.raises(UploadCompletionVerificationFailed):
                await complete()
        else:
            if scenario == "crash":
                with pytest.raises(ConnectionError, match="simulated"):
                    await complete()
            results = await asyncio.gather(*[complete() for _ in range(4)])
            assert len({r.version_id for r in results}) == 1
            reads = store.read_calls
            replay = await complete()
            assert replay.replayed and store.read_calls == reads
        async with factory() as db:
            tenant = await db.get(Tenant, seeded.principal.tenant_id)
            row = await db.get(UploadSession, seeded.session_id)
            versions = (
                await db.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.upload_session_id == seeded.session_id
                    )
                )
            ).all()
            assert tenant.reserved_storage_bytes == 0
            if scenario in {"wrong_hash", "foreign_metadata"}:
                assert row.status == "failed" and not versions and tenant.used_storage_bytes == 0
                assert (
                    store.delete_calls == 0
                )  # Unexpired capabilities must not recreate deleted objects.
            else:
                assert row.status == "completed" and len(versions) == 1
                assert tenant.used_storage_bytes == len(seeded.content)
                assert versions[0].content_sha256_verified_at is not None
        assert store.list_calls == 0 and store.complete_calls == 0 and store.head_calls == 0
        assert not store.range_requests
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("race", [False, True])
async def test_single_put_abort_tracks_retirement_and_releases_quota_once(race):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(factory, content=b"%PDF-1.7")

    class RetiringStore(CompletionObjectStore):
        retire_calls = 0

        async def retire_upload_object(self, **kwargs):
            self.retire_calls += 1
            if race and self.retire_calls == 1:
                return False
            assert kwargs["metadata"]["upload-session-id"] == str(seeded.session_id)
            self.readback_content = b""
            return True

    store = RetiringStore(seeded)
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
        if race:
            with pytest.raises(UploadAbortFailed):
                await service.abort(
                    principal=seeded.principal.context, session_id=seeded.session_id
                )
            async with factory() as db:
                row = await db.get(UploadSession, seeded.session_id)
                assert row.status == "aborted" and row.single_put_retired_at is None
        await service.abort(principal=seeded.principal.context, session_id=seeded.session_id)
        calls = store.retire_calls
        replay = await service.abort(
            principal=seeded.principal.context, session_id=seeded.session_id
        )
        assert replay.replayed and store.retire_calls == calls
        async with factory() as db:
            row = await db.get(UploadSession, seeded.session_id)
            tenant = await db.get(Tenant, seeded.principal.tenant_id)
            assert row.single_put_retired_at is not None and row.status == "aborted"
            assert (
                row.reserved_bytes == 0
                and tenant.reserved_storage_bytes == 0
                and tenant.used_storage_bytes == 0
            )
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario", ["expired", "failed", "aborted", "stale_good", "stale_missing", "stale_bad"]
)
async def test_single_put_background_cleanup_and_recovery(scenario):
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    factory = create_session_factory(engine)
    seeded = await _seed_upload(factory, content=b"%PDF-1.7")
    now = datetime.now(UTC)

    class CleanupStore(CompletionObjectStore):
        retired = 0

        async def list_incomplete_uploads(self, **kwargs):
            return ()

        async def read_object(self, **kwargs):
            if scenario == "stale_missing":
                raise ObjectStoreNotFound()
            return await super().read_object(**kwargs)

        async def retire_upload_object(self, **kwargs):
            self.retired += 1
            assert kwargs["metadata"]["version-id"] == str(seeded.pending_version_id)
            self.readback_content = b""
            return True

    store = CleanupStore(seeded)
    store.object_exists = True
    if scenario == "stale_bad":
        store.readback_content = b"%PDF-1.8"
    try:
        status = (
            "completing"
            if scenario.startswith("stale_")
            else "active"
            if scenario == "expired"
            else scenario
        )
        async with factory.begin() as db:
            await db.execute(
                update(UploadSession)
                .where(UploadSession.id == seeded.session_id)
                .values(
                    transport="single_put",
                    object_store_upload_id=None,
                    status=status,
                    expires_at=now - timedelta(days=2),
                    completion_started_at=now - timedelta(days=2),
                    reserved_bytes=0 if scenario == "aborted" else len(seeded.content),
                )
            )
            if scenario == "aborted":
                await db.execute(
                    update(Tenant)
                    .where(Tenant.id == seeded.principal.tenant_id)
                    .values(reserved_storage_bytes=0)
                )
        cleanup = UploadCleanupService(
            session_factory=factory,
            object_store=store,
            documents_bucket="documents",
            clock=lambda: now,
        )
        dry = await cleanup.run(dry_run=True)
        assert not dry.failed and dry.counters["sessionCandidates"] == 1 and store.retired == 0
        report = await cleanup.run()
        assert not report.failed, report.to_dict()
        async with factory() as db:
            row = await db.get(UploadSession, seeded.session_id)
            tenant = await db.get(Tenant, seeded.principal.tenant_id)
            assert tenant.reserved_storage_bytes == 0
            if scenario == "stale_good":
                assert row.status == "completed" and tenant.used_storage_bytes == len(
                    seeded.content
                )
                assert store.retired == 0
            else:
                assert (
                    row.status in {"aborted", "expired", "failed"}
                    and row.single_put_retired_at is not None
                )
                assert store.retired == 1 and tenant.used_storage_bytes == 0
        again = await cleanup.run()
        assert not again.failed and again.counters["sessionCandidates"] == 0
        assert store.delete_calls == 0 and store.complete_calls == 0 and store.list_calls == 0
    finally:
        await _cleanup_seeded(factory, seeded)
        await engine.dispose()
