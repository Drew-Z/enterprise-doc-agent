from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import Document, DocumentVersion, DocumentVersionStatus
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.object_store import (
    CompletedMultipartUpload,
    IncompleteUpload,
    MultipartUploadNotFound,
    ObjectHead,
    ObjectStoreNotFound,
    ObjectStoreUnavailable,
    UploadedPart,
)
from enterprise_doc_core.uploads import (
    UploadAbortConflict,
    UploadCleanupService,
    UploadPart,
    UploadSession,
    UploadSessionService,
    UploadSessionStatus,
)


@dataclass(frozen=True, slots=True)
class SeededSession:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    session_id: UUID
    pending_document_id: UUID
    pending_version_id: UUID
    object_key: str
    upload_id: str | None
    size_bytes: int
    content: bytes
    parts: tuple[UploadedPart, ...]

    @property
    def principal(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role=MembershipRole.OWNER.value,
        )


class AbortObjectStore:
    def __init__(self, seeded: SeededSession) -> None:
        self.seeded = seeded
        self.multipart_exists = seeded.upload_id is not None
        self.abort_error: Exception | None = None
        self.abort_calls = 0

    async def abort_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        assert bucket == "documents"
        assert key == self.seeded.object_key
        assert upload_id == self.seeded.upload_id
        self.abort_calls += 1
        if self.abort_error is not None:
            raise self.abort_error
        if not self.multipart_exists:
            raise MultipartUploadNotFound()
        self.multipart_exists = False


class CleanupObjectStore:
    def __init__(self) -> None:
        self.multipart_uploads: set[tuple[str, str]] = set()
        self.parts: dict[tuple[str, str], tuple[UploadedPart, ...]] = {}
        self.contents: dict[str, bytes] = {}
        self.object_metadata: dict[str, dict[str, str]] = {}
        self.object_sizes: dict[str, int] = {}
        self.object_checksums: dict[str, str | None] = {}
        self.object_etags: dict[str, str] = {}
        self.completed_objects: set[str] = set()
        self.incomplete_uploads: tuple[IncompleteUpload, ...] = ()
        self.abort_errors: dict[tuple[str, str], Exception] = {}
        self.delete_errors: dict[str, Exception] = {}
        self.abort_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.list_calls: list[tuple[str, str]] = []
        self.complete_calls: list[tuple[str, str]] = []

    def register_session(
        self,
        seeded: SeededSession,
        *,
        multipart_exists: bool = True,
        object_exists: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if seeded.upload_id is None:
            return
        identity = (seeded.object_key, seeded.upload_id)
        if multipart_exists:
            self.multipart_uploads.add(identity)
        self.parts[identity] = seeded.parts
        self.contents[seeded.object_key] = seeded.content
        self.object_metadata[seeded.object_key] = metadata or {
            "contract": "m1",
            "upload-session-id": str(seeded.session_id),
            "version-id": str(seeded.pending_version_id),
            "declared-size": str(seeded.size_bytes),
        }
        self.object_sizes[seeded.object_key] = seeded.size_bytes
        self.object_checksums[seeded.object_key] = "transport-checksum"
        self.object_etags[seeded.object_key] = '"completed-etag"'
        if object_exists:
            self.completed_objects.add(seeded.object_key)

    async def abort_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        assert bucket == "documents"
        identity = (key, upload_id)
        self.abort_calls.append(identity)
        error = self.abort_errors.get(identity)
        if error is not None:
            raise error
        if identity not in self.multipart_uploads:
            raise MultipartUploadNotFound()
        self.multipart_uploads.remove(identity)

    async def list_parts(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> tuple[UploadedPart, ...]:
        assert bucket == "documents"
        identity = (key, upload_id)
        self.list_calls.append(identity)
        if identity not in self.multipart_uploads:
            raise MultipartUploadNotFound()
        return self.parts[identity]

    async def complete_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
        parts: tuple[UploadedPart, ...],
    ) -> CompletedMultipartUpload:
        assert bucket == "documents"
        identity = (key, upload_id)
        self.complete_calls.append(identity)
        if identity not in self.multipart_uploads:
            raise MultipartUploadNotFound()
        assert parts == self.parts[identity]
        self.multipart_uploads.remove(identity)
        self.completed_objects.add(key)
        return CompletedMultipartUpload(
            etag=self.object_etags[key],
            checksum_sha256_b64=self.object_checksums[key],
        )

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        assert bucket == "documents"
        if key not in self.completed_objects:
            raise ObjectStoreNotFound()
        return ObjectHead(
            size_bytes=self.object_sizes[key],
            etag=self.object_etags[key],
            checksum_sha256_b64=self.object_checksums[key],
            content_type="application/octet-stream",
            metadata=self.object_metadata[key],
        )

    async def get_range(
        self,
        *,
        bucket: str,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        assert bucket == "documents"
        return self.contents[key][start : end_inclusive + 1]

    async def delete_object(self, *, bucket: str, key: str) -> None:
        assert bucket == "documents"
        self.delete_calls.append(key)
        error = self.delete_errors.get(key)
        if error is not None:
            raise error
        if key not in self.completed_objects:
            raise ObjectStoreNotFound()
        self.completed_objects.remove(key)

    async def list_incomplete_uploads(
        self,
        *,
        bucket: str,
        prefix: str,
    ) -> tuple[IncompleteUpload, ...]:
        assert bucket == "documents"
        assert prefix == "m1/uploads/"
        return self.incomplete_uploads


class CommitAcknowledgementLostTransaction:
    def __init__(self, inner: Any, *, fail_after_commit: bool) -> None:
        self.inner = inner
        self.fail_after_commit = fail_after_commit

    async def __aenter__(self) -> Any:
        return await self.inner.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        result = await self.inner.__aexit__(exc_type, exc_value, traceback)
        if self.fail_after_commit and exc_type is None:
            raise ConnectionError("commit acknowledgement lost")
        return result


class CommitAcknowledgementLostSessionFactory:
    def __init__(self, inner: Any, *, fail_on_begin: int) -> None:
        self.inner = inner
        self.fail_on_begin = fail_on_begin
        self.begin_calls = 0

    def __call__(self) -> Any:
        return self.inner()

    def begin(self) -> CommitAcknowledgementLostTransaction:
        self.begin_calls += 1
        return CommitAcknowledgementLostTransaction(
            self.inner.begin(),
            fail_after_commit=self.begin_calls == self.fail_on_begin,
        )


async def _seed_session(
    session_factory: Any,
    *,
    status: UploadSessionStatus = UploadSessionStatus.ACTIVE,
    reserved: bool = True,
    with_upload_id: bool = True,
    content: bytes = b"%PDF-1.7\n",
    expires_at: datetime | None = None,
    completion_started_at: datetime | None = None,
    cleanup_claimed_at: datetime | None = None,
    cleanup_claim_token: UUID | None = None,
    verified_part: bool = False,
    last_error_code: str | None = None,
) -> SeededSession:
    tenant_id = uuid4()
    actor_id = uuid4()
    membership_id = uuid4()
    session_id = uuid4()
    pending_document_id = uuid4()
    pending_version_id = uuid4()
    size_bytes = len(content)
    upload_id = f"upload-{session_id}" if with_upload_id else None
    object_key = f"m1/uploads/{session_id.hex}/{pending_version_id.hex}"
    reserved_bytes = size_bytes if reserved else 0
    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    parts = (
        UploadedPart(
            part_number=1,
            size_bytes=size_bytes,
            etag='"etag-one"',
            checksum_sha256_b64=checksum,
        ),
    )
    async with session_factory.begin() as database:
        database.add(
            Tenant(
                id=tenant_id,
                name="Cleanup fixture",
                slug=f"cleanup-{tenant_id}",
                quota_bytes=1024,
                reserved_storage_bytes=reserved_bytes,
                used_storage_bytes=size_bytes if status is UploadSessionStatus.COMPLETED else 0,
            )
        )
        database.add(User(id=actor_id, email=f"{actor_id}@example.test"))
        await database.flush()
        database.add(
            Membership(
                id=membership_id,
                tenant_id=tenant_id,
                user_id=actor_id,
                role=MembershipRole.OWNER.value,
            )
        )
        upload_session = UploadSession(
            id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            pending_document_id=pending_document_id,
            pending_version_id=pending_version_id,
            status=status.value,
            idempotency_key=f"cleanup-{session_id}",
            request_fingerprint="a" * 64,
            object_key=object_key,
            object_store_upload_id=upload_id,
            original_filename="cleanup.pdf",
            extension=".pdf",
            declared_media_type="application/pdf",
            size_bytes=size_bytes,
            declared_sha256=hashlib.sha256(content).hexdigest(),
            part_size_bytes=size_bytes,
            expected_part_count=1,
            reserved_bytes=reserved_bytes,
            expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
            completion_started_at=completion_started_at,
            completed_at=datetime.now(UTC) if status is UploadSessionStatus.COMPLETED else None,
            last_error_code=last_error_code,
            cleanup_claimed_at=cleanup_claimed_at,
            cleanup_claim_token=cleanup_claim_token,
        )
        database.add(upload_session)
        await database.flush()
        if verified_part:
            now = datetime.now(UTC)
            database.add(
                UploadPart(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    upload_session_id=session_id,
                    part_number=1,
                    expected_checksum_sha256=checksum,
                    observed_checksum_sha256=checksum,
                    etag=parts[0].etag,
                    size_bytes=size_bytes,
                    observation_version=1,
                    observed_at=now,
                    verified_at=now,
                )
            )
        if status is UploadSessionStatus.COMPLETED:
            document = Document(
                id=pending_document_id,
                tenant_id=tenant_id,
                created_by=actor_id,
                title="cleanup.pdf",
            )
            database.add(document)
            await database.flush()
            version = DocumentVersion(
                id=pending_version_id,
                tenant_id=tenant_id,
                document_id=pending_document_id,
                upload_session_id=session_id,
                version_number=1,
                status=DocumentVersionStatus.UPLOADED.value,
                object_key=object_key,
                original_filename="cleanup.pdf",
                declared_media_type="application/pdf",
                detected_media_type="application/pdf",
                size_bytes=size_bytes,
                declared_sha256="b" * 64,
                transport_checksum_sha256="transport",
                created_by=actor_id,
            )
            database.add(version)
            await database.flush()
            upload_session.document_version_id = pending_version_id
    return SeededSession(
        tenant_id=tenant_id,
        actor_id=actor_id,
        membership_id=membership_id,
        session_id=session_id,
        pending_document_id=pending_document_id,
        pending_version_id=pending_version_id,
        object_key=object_key,
        upload_id=upload_id,
        size_bytes=size_bytes,
        content=content,
        parts=parts,
    )


async def _cleanup_session(session_factory: Any, seeded: SeededSession) -> None:
    async with session_factory.begin() as database:
        await database.execute(
            update(UploadSession)
            .where(UploadSession.id == seeded.session_id)
            .values(document_version_id=None)
        )
        await database.execute(
            delete(DocumentVersion).where(DocumentVersion.upload_session_id == seeded.session_id)
        )
        await database.execute(delete(Document).where(Document.id == seeded.pending_document_id))
        await database.execute(delete(UploadSession).where(UploadSession.id == seeded.session_id))
        await database.execute(delete(Membership).where(Membership.id == seeded.membership_id))
        await database.execute(delete(User).where(User.id == seeded.actor_id))
        await database.execute(delete(Tenant).where(Tenant.id == seeded.tenant_id))


def _service(session_factory: Any, store: AbortObjectStore) -> UploadSessionService:
    return UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket="documents",
        settings=ApiSettings(_env_file=None).upload,
    )


def _cleanup_service(
    session_factory: Any,
    store: CleanupObjectStore,
    *,
    now: datetime,
    batch_size: int = 10,
) -> UploadCleanupService:
    settings = ApiSettings(
        _env_file=None,
        upload={
            "cleanup_batch_size": batch_size,
            "cleanup_expiry_grace_seconds": 0,
            "cleanup_completing_grace_seconds": 60,
            "cleanup_orphan_grace_seconds": 60,
            "cleanup_claim_ttl_seconds": 30,
        },
    )
    return UploadCleanupService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket="documents",
        settings=settings.upload,
        clock=lambda: now,
    )


@pytest.mark.integration
async def test_abort_is_idempotent_and_releases_quota_once() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(session_factory)
    store = AbortObjectStore(seeded)
    service = _service(session_factory, store)

    try:
        first = await service.abort(principal=seeded.principal, session_id=seeded.session_id)
        async with session_factory() as database:
            after_first = await database.get(UploadSession, seeded.session_id)
            assert after_first is not None
            first_aborted_at = after_first.aborted_at

        second = await service.abort(principal=seeded.principal, session_id=seeded.session_id)

        assert first.replayed is False
        assert second.replayed is True
        assert store.abort_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ABORTED.value
            assert upload_session.aborted_at == first_aborted_at
            assert upload_session.object_store_upload_id is None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_abort_remote_failure_is_retryable_without_releasing_quota_twice() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(session_factory)
    store = AbortObjectStore(seeded)
    store.abort_error = ObjectStoreUnavailable()
    service = _service(session_factory, store)

    try:
        with pytest.raises(ObjectStoreUnavailable):
            await service.abort(principal=seeded.principal, session_id=seeded.session_id)
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ABORTED.value
            assert upload_session.object_store_upload_id == seeded.upload_id
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0

        store.abort_error = None
        result = await service.abort(principal=seeded.principal, session_id=seeded.session_id)

        assert result.replayed is True
        assert store.abort_calls == 2
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.object_store_upload_id is None
            assert tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("fail_on_begin", [1, 2])
async def test_abort_recovers_commit_acknowledgement_loss(fail_on_begin: int) -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(session_factory)
    store = AbortObjectStore(seeded)
    faulting_factory = CommitAcknowledgementLostSessionFactory(
        session_factory,
        fail_on_begin=fail_on_begin,
    )
    service = _service(faulting_factory, store)

    try:
        result = await service.abort(principal=seeded.principal, session_id=seeded.session_id)

        assert result.status == UploadSessionStatus.ABORTED.value
        assert store.abort_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ABORTED.value
            assert upload_session.object_store_upload_id is None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [UploadSessionStatus.COMPLETING, UploadSessionStatus.COMPLETED],
)
async def test_abort_conflicts_with_completing_and_completed_sessions(
    status: UploadSessionStatus,
) -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        status=status,
        reserved=status is UploadSessionStatus.COMPLETING,
        with_upload_id=True,
    )
    store = AbortObjectStore(seeded)
    service = _service(session_factory, store)

    try:
        with pytest.raises(UploadAbortConflict):
            await service.abort(principal=seeded.principal, session_id=seeded.session_id)

        assert store.abort_calls == 0
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == status.value
            assert upload_session.object_store_upload_id == seeded.upload_id
            assert tenant.reserved_storage_bytes == (
                seeded.size_bytes if status is UploadSessionStatus.COMPLETING else 0
            )
            assert tenant.used_storage_bytes == (
                seeded.size_bytes if status is UploadSessionStatus.COMPLETED else 0
            )
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_dry_run_discovers_without_claiming_or_mutating() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    store = CleanupObjectStore()
    store.register_session(seeded)
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run(dry_run=True)

        assert report.failed is False
        assert report.counters["sessionCandidates"] == 1
        assert report.counters["expiryCandidates"] == 1
        assert report.counters["sessionsClaimed"] == 0
        assert store.abort_calls == []
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ACTIVE.value
            assert upload_session.cleanup_claim_token is None
            assert upload_session.reserved_bytes == seeded.size_bytes
            assert tenant.reserved_storage_bytes == seeded.size_bytes
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_expires_session_aborts_multipart_and_replays_without_side_effects() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    store = CleanupObjectStore()
    store.register_session(seeded)
    service = _cleanup_service(session_factory, store, now=now)

    try:
        first = await service.run()
        second = await service.run()

        assert first.failed is False
        assert first.counters["sessionsExpired"] == 1
        assert first.counters["reservationsReleased"] == 1
        assert first.counters["multipartAborted"] == 1
        assert second.counters["sessionCandidates"] == 0
        assert store.abort_calls == [(seeded.object_key, seeded.upload_id)]
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.EXPIRED.value
            assert upload_session.object_store_upload_id is None
            assert upload_session.cleanup_claim_token is None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_recovers_only_after_claim_lease_expires() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
        cleanup_claimed_at=now,
        cleanup_claim_token=uuid4(),
    )
    store = CleanupObjectStore()
    store.register_session(seeded)
    service = _cleanup_service(session_factory, store, now=now)

    try:
        fresh = await service.run()
        assert fresh.counters["sessionCandidates"] == 0
        assert store.abort_calls == []

        async with session_factory.begin() as database:
            await database.execute(
                update(UploadSession)
                .where(UploadSession.id == seeded.session_id)
                .values(cleanup_claimed_at=now - timedelta(seconds=31))
            )

        recovered = await service.run()

        assert recovered.failed is False
        assert recovered.counters["sessionsExpired"] == 1
        assert len(store.abort_calls) == 1
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_concurrent_cleanup_workers_claim_disjoint_bounded_rows() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    first = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    second = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    store = CleanupObjectStore()
    store.register_session(first)
    store.register_session(second)
    service_one = _cleanup_service(session_factory, store, now=now, batch_size=1)
    service_two = _cleanup_service(session_factory, store, now=now, batch_size=1)

    try:
        reports = await asyncio.gather(service_one.run(), service_two.run())

        assert all(report.failed is False for report in reports)
        assert sum(report.counters["sessionsClaimed"] for report in reports) == 2
        assert sorted(store.abort_calls) == sorted(
            [
                (first.object_key, first.upload_id),
                (second.object_key, second.upload_id),
            ]
        )
        async with session_factory() as database:
            rows = tuple(
                (
                    await database.scalars(
                        select(UploadSession).where(
                            UploadSession.id.in_((first.session_id, second.session_id))
                        )
                    )
                ).all()
            )
            assert {row.status for row in rows} == {UploadSessionStatus.EXPIRED.value}
            assert all(row.object_store_upload_id is None for row in rows)
    finally:
        await _cleanup_session(session_factory, first)
        await _cleanup_session(session_factory, second)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_claim_query_skips_a_locked_eligible_row() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    locked = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    available = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    store = CleanupObjectStore()
    store.register_session(locked)
    store.register_session(available)
    service = _cleanup_service(session_factory, store, now=now, batch_size=1)
    lock_session = session_factory()

    try:
        async with session_factory.begin() as database:
            await database.execute(
                update(UploadSession)
                .where(UploadSession.id == locked.session_id)
                .values(updated_at=now - timedelta(minutes=2))
            )
            await database.execute(
                update(UploadSession)
                .where(UploadSession.id == available.session_id)
                .values(updated_at=now - timedelta(minutes=1))
            )
        await lock_session.begin()
        locked_row = await lock_session.scalar(
            select(UploadSession).where(UploadSession.id == locked.session_id).with_for_update()
        )
        assert locked_row is not None

        report = await asyncio.wait_for(service.run(), timeout=2)

        assert report.failed is False
        assert report.counters["sessionsClaimed"] == 1
        assert store.abort_calls == [(available.object_key, available.upload_id)]
        async with session_factory() as database:
            locked_state = await database.get(UploadSession, locked.session_id)
            available_state = await database.get(UploadSession, available.session_id)
            assert locked_state is not None and available_state is not None
            assert locked_state.status == UploadSessionStatus.ACTIVE.value
            assert available_state.status == UploadSessionStatus.EXPIRED.value
    finally:
        await lock_session.rollback()
        await lock_session.close()
        await _cleanup_session(session_factory, locked)
        await _cleanup_session(session_factory, available)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_isolates_partial_object_store_failure_per_session() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    failed = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    successful = await _seed_session(
        session_factory,
        expires_at=now - timedelta(minutes=5),
    )
    store = CleanupObjectStore()
    store.register_session(failed)
    store.register_session(successful)
    store.abort_errors[(failed.object_key, str(failed.upload_id))] = ObjectStoreUnavailable()
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run()

        assert report.failed is True
        assert report.exceptions_by_class == {"ObjectStoreUnavailable": 1}
        assert report.counters["sessionsExpired"] == 2
        assert report.counters["reservationsReleased"] == 2
        assert len(store.abort_calls) == 2
        async with session_factory() as database:
            failed_row = await database.get(UploadSession, failed.session_id)
            successful_row = await database.get(UploadSession, successful.session_id)
            failed_tenant = await database.get(Tenant, failed.tenant_id)
            successful_tenant = await database.get(Tenant, successful.tenant_id)
            assert failed_row is not None and successful_row is not None
            assert failed_tenant is not None and successful_tenant is not None
            assert failed_row.status == UploadSessionStatus.EXPIRED.value
            assert failed_row.object_store_upload_id == failed.upload_id
            assert failed_row.cleanup_claim_token is not None
            assert successful_row.object_store_upload_id is None
            assert failed_tenant.reserved_storage_bytes == 0
            assert successful_tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, failed)
        await _cleanup_session(session_factory, successful)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_releases_initialization_failure_and_resolves_missing_multipart() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        status=UploadSessionStatus.FAILED,
        reserved=True,
        last_error_code="upload_initialization_abort_failed",
    )
    store = CleanupObjectStore()
    store.register_session(seeded, multipart_exists=False)
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run()

        assert report.failed is False
        assert report.counters["reservationRepairCandidates"] == 1
        assert report.counters["reservationsReleased"] == 1
        assert report.counters["multipartMissing"] == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.object_store_upload_id is None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "content",
        "multipart_exists",
        "object_exists",
        "ambiguous_metadata",
        "expected_counter",
        "expected_failed",
    ),
    [
        (b"%PDF-1.7\n", True, False, False, "staleCompleted", False),
        (b"%PDF-1.7\n", False, True, False, "staleCompleted", False),
        (b"not-pdf", True, False, False, "staleFailedInvalidOwned", False),
        (b"not-pdf", False, True, False, "staleFailedInvalidOwned", False),
        (b"%PDF-1.7\n", False, False, False, "staleFailedMissing", False),
        (b"not-pdf", False, True, True, "staleFailedAmbiguous", True),
    ],
)
async def test_cleanup_reconciles_stale_completion_outcomes(
    content: bytes,
    multipart_exists: bool,
    object_exists: bool,
    ambiguous_metadata: bool,
    expected_counter: str,
    expected_failed: bool,
) -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        status=UploadSessionStatus.COMPLETING,
        content=content,
        completion_started_at=now - timedelta(minutes=5),
        verified_part=True,
    )
    store = CleanupObjectStore()
    store.register_session(
        seeded,
        multipart_exists=multipart_exists,
        object_exists=object_exists,
        metadata=(
            {"contract": "m1", "upload-session-id": str(uuid4())} if ambiguous_metadata else None
        ),
    )
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run()

        assert report.failed is expected_failed
        assert report.counters[expected_counter] == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            if expected_counter == "staleCompleted":
                version = await database.get(DocumentVersion, seeded.pending_version_id)
                assert upload_session.status == UploadSessionStatus.COMPLETED.value
                assert upload_session.document_version_id == seeded.pending_version_id
                assert version is not None
                assert tenant.used_storage_bytes == seeded.size_bytes
            else:
                assert upload_session.status == UploadSessionStatus.FAILED.value
                assert upload_session.document_version_id is None
                assert tenant.used_storage_bytes == 0
        if expected_counter == "staleFailedInvalidOwned":
            assert store.delete_calls == [seeded.object_key]
            follow_up = await service.run()
            assert follow_up.failed is False
        elif expected_counter == "staleFailedMissing":
            follow_up = await service.run()
            assert follow_up.failed is False
        elif expected_counter == "staleFailedAmbiguous":
            assert report.counters["ownershipAmbiguous"] == 1
            assert store.delete_calls == []
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_retries_owned_invalid_object_after_delete_failure() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        status=UploadSessionStatus.COMPLETING,
        content=b"not-pdf",
        completion_started_at=now - timedelta(minutes=5),
        verified_part=True,
    )
    store = CleanupObjectStore()
    store.register_session(seeded)
    store.delete_errors[seeded.object_key] = ObjectStoreUnavailable()
    service = _cleanup_service(session_factory, store, now=now)

    try:
        first = await service.run()

        assert first.failed is True
        assert first.exceptions_by_class == {"ObjectStoreUnavailable": 1}
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            assert upload_session is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.object_store_upload_id == seeded.upload_id
            assert upload_session.cleanup_claim_token is None
        assert seeded.object_key in store.completed_objects

        store.delete_errors.clear()
        second = await service.run()

        assert second.failed is False
        assert second.counters["completedObjectsDeleted"] == 1
        assert store.delete_calls == [seeded.object_key, seeded.object_key]
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            assert upload_session is not None
            assert upload_session.object_store_upload_id is None
            assert upload_session.cleanup_claim_token is None
        assert seeded.object_key not in store.completed_objects
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_keeps_ambiguous_failed_object_for_manual_review() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_session(
        session_factory,
        status=UploadSessionStatus.FAILED,
        reserved=False,
        last_error_code="upload_completion_verification_failed",
    )
    store = CleanupObjectStore()
    store.register_session(
        seeded,
        multipart_exists=False,
        object_exists=True,
        metadata={"contract": "m1", "upload-session-id": str(uuid4())},
    )
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run()

        assert report.failed is True
        assert report.counters["ownershipAmbiguous"] == 1
        assert report.exceptions_by_class == {"UploadCleanupOwnershipAmbiguous": 1}
        assert store.delete_calls == []
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            assert upload_session is not None
            assert upload_session.object_store_upload_id == seeded.upload_id
            assert upload_session.cleanup_claim_token is not None
    finally:
        await _cleanup_session(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_orphan_scan_aborts_only_old_parseable_unowned_uploads() -> None:
    now = datetime.now(UTC)
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    live = await _seed_session(session_factory, reserved=True)
    orphan_session_id = uuid4()
    orphan_version_id = uuid4()
    orphan_key = f"m1/uploads/{orphan_session_id.hex}/{orphan_version_id.hex}"
    orphan_upload_id = f"upload-{orphan_session_id}"
    young_key = f"m1/uploads/{uuid4().hex}/{uuid4().hex}"
    naive_key = f"m1/uploads/{uuid4().hex}/{uuid4().hex}"
    store = CleanupObjectStore()
    store.multipart_uploads.update(
        {
            (orphan_key, orphan_upload_id),
            (live.object_key, str(live.upload_id)),
        }
    )
    store.incomplete_uploads = (
        IncompleteUpload(live.object_key, str(live.upload_id), now - timedelta(minutes=5)),
        IncompleteUpload(young_key, "young", now - timedelta(seconds=30)),
        IncompleteUpload("m1/uploads/not-random", "malformed", now - timedelta(minutes=5)),
        IncompleteUpload(naive_key, "naive", datetime.now()),
        IncompleteUpload(orphan_key, orphan_upload_id, now - timedelta(minutes=5)),
    )
    service = _cleanup_service(session_factory, store, now=now)

    try:
        report = await service.run()

        assert report.failed is False
        assert report.counters["orphanUploadsScanned"] == 5
        assert report.counters["orphanCandidates"] == 2
        assert report.counters["orphanSkippedOwned"] == 1
        assert report.counters["orphanSkippedMalformed"] == 1
        assert report.counters["orphanSkippedTimestamp"] == 2
        assert report.counters["orphanAborted"] == 1
        assert store.abort_calls == [(orphan_key, orphan_upload_id)]
        assert (live.object_key, str(live.upload_id)) in store.multipart_uploads
    finally:
        await _cleanup_session(session_factory, live)
        await engine.dispose()
