from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    Document,
    DocumentEnvelopeViolation,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.object_store import (
    Boto3MultipartObjectStore,
    CompletedMultipartUpload,
    MultipartUploadNotFound,
    ObjectHead,
    UploadedPart,
)
from enterprise_doc_core.uploads import (
    CompleteUploadPartInput,
    CompleteUploadSessionInput,
    UploadCompletionPartsInvalid,
    UploadCompletionVerificationFailed,
    UploadPart,
    UploadSession,
    UploadSessionExpired,
    UploadSessionNotActive,
    UploadSessionService,
    UploadSessionStatus,
)
from enterprise_doc_core.uploads.service import UploadCreationService

MIB = 1024**2


@dataclass(frozen=True, slots=True)
class FixturePrincipal:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    token: str

    @property
    def context(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role=MembershipRole.OWNER.value,
        )


@dataclass(frozen=True, slots=True)
class SeededUpload:
    principal: FixturePrincipal
    session_id: UUID
    pending_document_id: UUID
    pending_version_id: UUID
    content: bytes
    parts: tuple[UploadedPart, ...]


class TokenPrincipalResolver:
    def __init__(self, principals: list[FixturePrincipal]) -> None:
        self.principals = {principal.token: principal.context for principal in principals}

    async def resolve(self, token: str) -> PrincipalContext:
        return self.principals[token]


class CompletionObjectStore:
    def __init__(self, seeded: SeededUpload) -> None:
        self.seeded = seeded
        self.multipart_exists = True
        self.object_exists = False
        self.listed_parts = seeded.parts
        self.metadata = {
            "contract": "m1",
            "upload-session-id": str(seeded.session_id),
            "version-id": str(seeded.pending_version_id),
            "declared-size": str(len(seeded.content)),
        }
        self.complete_calls = 0
        self.list_calls = 0
        self.head_calls = 0
        self.delete_calls = 0
        self.transport_checksum = "transport-checksum"
        self.object_etag = '"completed-etag"'
        self.head_size_bytes = len(seeded.content)
        self.head_etag = self.object_etag
        self.head_checksum: str | None = self.transport_checksum

    async def list_parts(self, **_: object) -> tuple[UploadedPart, ...]:
        self.list_calls += 1
        if not self.multipart_exists:
            raise MultipartUploadNotFound()
        return self.listed_parts

    async def complete_upload(self, **_: object) -> CompletedMultipartUpload:
        self.complete_calls += 1
        if not self.multipart_exists:
            raise MultipartUploadNotFound()
        self.multipart_exists = False
        self.object_exists = True
        return CompletedMultipartUpload(
            etag=self.object_etag,
            checksum_sha256_b64=self.transport_checksum,
        )

    async def head_object(self, **_: object) -> ObjectHead:
        self.head_calls += 1
        if not self.object_exists:
            raise AssertionError("head called before object completion")
        return ObjectHead(
            size_bytes=self.head_size_bytes,
            etag=self.head_etag,
            checksum_sha256_b64=self.head_checksum,
            content_type="application/octet-stream",
            metadata=self.metadata,
        )

    async def get_range(
        self,
        *,
        start: int,
        end_inclusive: int,
        **_: object,
    ) -> bytes:
        return self.seeded.content[start : end_inclusive + 1]

    async def delete_object(self, **_: object) -> None:
        self.delete_calls += 1
        self.object_exists = False


class CrashBeforeFinalizationService(UploadSessionService):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.fail_once = True

    async def _finalize_completion(self, **kwargs: object):
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError("simulated post-object completion crash")
        return await super()._finalize_completion(**kwargs)


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
            raise ConnectionError("finalization commit acknowledgement lost")
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


def _principal(label: str, *, tenant_id: UUID | None = None) -> FixturePrincipal:
    return FixturePrincipal(
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        actor_id=uuid4(),
        membership_id=uuid4(),
        token=f"complete-{label}",
    )


def _checksum(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def _auth(principal: FixturePrincipal) -> dict[str, str]:
    return {"Authorization": f"Bearer {principal.token}"}


def _completion_request(parts: tuple[UploadedPart, ...]) -> CompleteUploadSessionInput:
    return CompleteUploadSessionInput(
        parts=tuple(
            CompleteUploadPartInput(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
                etag=part.etag,
                checksum_sha256_b64=part.checksum_sha256_b64,
            )
            for part in parts
        )
    )


async def _seed_upload(session_factory, *, content: bytes) -> SeededUpload:
    principal = _principal(str(uuid4()))
    session_id = uuid4()
    pending_document_id = uuid4()
    pending_version_id = uuid4()
    first = content[:5]
    second = content[5:]
    parts = (
        UploadedPart(1, len(first), '"etag-one"', _checksum(first)),
        UploadedPart(2, len(second), '"etag-two"', _checksum(second)),
    )
    async with session_factory.begin() as database:
        database.add(
            Tenant(
                id=principal.tenant_id,
                name="Completion fixture",
                slug=f"completion-{principal.tenant_id}",
                quota_bytes=1024,
                reserved_storage_bytes=len(content),
            )
        )
        database.add(User(id=principal.actor_id, email=f"{principal.actor_id}@example.test"))
        await database.flush()
        database.add(
            Membership(
                id=principal.membership_id,
                tenant_id=principal.tenant_id,
                user_id=principal.actor_id,
                role=MembershipRole.OWNER.value,
            )
        )
        database.add(
            UploadSession(
                id=session_id,
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
                pending_document_id=pending_document_id,
                pending_version_id=pending_version_id,
                status=UploadSessionStatus.ACTIVE.value,
                idempotency_key=f"complete-{session_id}",
                request_fingerprint="a" * 64,
                object_key=f"m1/uploads/{session_id.hex}/{pending_version_id.hex}",
                object_store_upload_id=f"upload-{session_id}",
                original_filename="fixture.pdf",
                extension=".pdf",
                declared_media_type="application/pdf",
                size_bytes=len(content),
                declared_sha256=hashlib.sha256(content).hexdigest(),
                part_size_bytes=len(first),
                expected_part_count=2,
                reserved_bytes=len(content),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await database.flush()
        for part in parts:
            database.add(
                UploadPart(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    upload_session_id=session_id,
                    part_number=part.part_number,
                    expected_checksum_sha256=part.checksum_sha256_b64,
                )
            )
    return SeededUpload(
        principal=principal,
        session_id=session_id,
        pending_document_id=pending_document_id,
        pending_version_id=pending_version_id,
        content=content,
        parts=parts,
    )


async def _cleanup_seeded(session_factory, seeded: SeededUpload) -> None:
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
        await database.execute(
            delete(Membership).where(Membership.id == seeded.principal.membership_id)
        )
        await database.execute(delete(User).where(User.id == seeded.principal.actor_id))
        await database.execute(delete(Tenant).where(Tenant.id == seeded.principal.tenant_id))


@pytest.mark.integration
async def test_real_minio_concurrent_complete_creates_one_uploaded_version() -> None:
    settings = ApiSettings(
        _env_file=None,
        upload={"preferred_part_size_bytes": 5 * MIB},
    )
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    owner = _principal("real-owner")
    intruder = _principal("real-intruder", tenant_id=owner.tenant_id)
    object_store = Boto3MultipartObjectStore(settings=settings.object_store)
    creation_service = UploadCreationService(
        session_factory=session_factory,
        settings=settings.upload,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
    )
    session_service = UploadSessionService(
        session_factory=session_factory,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    content = b"%PDF-1.7\n" + b"slice-six" * 1024
    session_id: UUID | None = None
    object_key: str | None = None
    pending_document_id: UUID | None = None

    try:
        async with session_factory.begin() as database:
            database.add(
                Tenant(
                    id=owner.tenant_id,
                    name="Real completion",
                    slug=f"real-completion-{owner.tenant_id}",
                    quota_bytes=20 * MIB,
                )
            )
            for principal in (owner, intruder):
                database.add(
                    User(id=principal.actor_id, email=f"{principal.actor_id}@example.test")
                )
            await database.flush()
            for principal in (owner, intruder):
                database.add(
                    Membership(
                        id=principal.membership_id,
                        tenant_id=principal.tenant_id,
                        user_id=principal.actor_id,
                        role=MembershipRole.OWNER.value,
                    )
                )

        app = create_app(
            settings=settings,
            checkers=[],
            principal_resolver=TokenPrincipalResolver([owner, intruder]),
            upload_creation_service=creation_service,
            upload_session_service=session_service,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as api:
            created = await api.post(
                "/api/upload-sessions",
                headers={**_auth(owner), "Idempotency-Key": "complete-real-minio"},
                json={
                    "filename": "complete.pdf",
                    "sizeBytes": len(content),
                    "mediaType": "application/pdf",
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            assert created.status_code == 201
            session_id = UUID(created.json()["sessionId"])
            signed = await api.post(
                f"/api/upload-sessions/{session_id}/parts/1/presign",
                headers=_auth(owner),
                json={"sizeBytes": len(content), "checksumSha256": _checksum(content)},
            )
            assert signed.status_code == 200
            async with httpx.AsyncClient(timeout=30, trust_env=False) as transfer:
                uploaded = await transfer.put(
                    signed.json()["url"],
                    headers=signed.json()["headers"],
                    content=content,
                )
            assert uploaded.status_code == 200
            resumed = await api.get(
                f"/api/upload-sessions/{session_id}",
                headers=_auth(owner),
            )
            assert resumed.status_code == 200
            complete_payload = {"parts": resumed.json()["uploadedParts"]}

            hidden = await api.post(
                f"/api/upload-sessions/{session_id}/complete",
                headers=_auth(intruder),
                json=complete_payload,
            )
            assert hidden.status_code == 404

            first, second = await asyncio.gather(
                api.post(
                    f"/api/upload-sessions/{session_id}/complete",
                    headers=_auth(owner),
                    json=complete_payload,
                ),
                api.post(
                    f"/api/upload-sessions/{session_id}/complete",
                    headers=_auth(owner),
                    json=complete_payload,
                ),
            )
            assert [first.status_code, second.status_code] == [200, 200]
            assert {first.json()["versionId"], second.json()["versionId"]} == {
                first.json()["versionId"]
            }
            assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [
                False,
                True,
            ]
            replay = await api.post(
                f"/api/upload-sessions/{session_id}/complete",
                headers=_auth(owner),
                json=complete_payload,
            )
            assert replay.status_code == 200
            assert replay.json()["versionId"] == first.json()["versionId"]
            assert replay.json()["replayed"] is True

        async with session_factory() as database:
            upload_session = await database.get(UploadSession, session_id)
            tenant = await database.get(Tenant, owner.tenant_id)
            version = await database.scalar(
                select(DocumentVersion).where(DocumentVersion.upload_session_id == session_id)
            )
            assert upload_session is not None and tenant is not None and version is not None
            document_count = await database.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.id == upload_session.pending_document_id)
            )
            version_count = await database.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.upload_session_id == session_id)
            )
            object_key = upload_session.object_key
            pending_document_id = upload_session.pending_document_id
            assert upload_session.status == UploadSessionStatus.COMPLETED.value
            assert upload_session.document_version_id == version.id
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == len(content)
            assert version.id == upload_session.pending_version_id
            assert version.document_id == upload_session.pending_document_id
            assert version.status == DocumentVersionStatus.UPLOADED.value
            assert version.detected_media_type == "application/pdf"
            assert version.content_sha256_verified_at is None
            assert version.transport_checksum_sha256 is not None
            assert document_count == 1
            assert version_count == 1
    finally:
        if object_key is not None:
            await object_store.delete_object(
                bucket=settings.object_store.documents_bucket,
                key=object_key,
            )
        if session_id is not None:
            async with session_factory.begin() as database:
                await database.execute(
                    update(UploadSession)
                    .where(UploadSession.id == session_id)
                    .values(document_version_id=None)
                )
                await database.execute(
                    delete(DocumentVersion).where(DocumentVersion.upload_session_id == session_id)
                )
                if pending_document_id is not None:
                    await database.execute(
                        delete(Document).where(Document.id == pending_document_id)
                    )
                await database.execute(delete(UploadSession).where(UploadSession.id == session_id))
        async with session_factory.begin() as database:
            await database.execute(
                delete(Membership).where(
                    Membership.id.in_([owner.membership_id, intruder.membership_id])
                )
            )
            await database.execute(
                delete(User).where(User.id.in_([owner.actor_id, intruder.actor_id]))
            )
            await database.execute(delete(Tenant).where(Tenant.id == owner.tenant_id))
        await object_store.close()
        await engine.dispose()


@pytest.mark.integration
async def test_completion_validates_ordered_parts_before_completing() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    valid = _completion_request(seeded.parts)

    try:
        invalid_requests = [
            CompleteUploadSessionInput(parts=valid.parts[:1]),
            CompleteUploadSessionInput(parts=(valid.parts[1], valid.parts[0])),
            CompleteUploadSessionInput(parts=(valid.parts[0], valid.parts[0])),
            CompleteUploadSessionInput(
                parts=(
                    valid.parts[0],
                    valid.parts[1],
                    CompleteUploadPartInput(3, 1, '"extra"', _checksum(b"x")),
                )
            ),
            CompleteUploadSessionInput(
                parts=(
                    CompleteUploadPartInput(
                        valid.parts[0].part_number,
                        valid.parts[0].size_bytes + 1,
                        valid.parts[0].etag,
                        valid.parts[0].checksum_sha256_b64,
                    ),
                    valid.parts[1],
                )
            ),
            CompleteUploadSessionInput(
                parts=(
                    CompleteUploadPartInput(
                        valid.parts[0].part_number,
                        valid.parts[0].size_bytes,
                        valid.parts[0].etag,
                        _checksum(b"wrong-checksum"),
                    ),
                    valid.parts[1],
                )
            ),
        ]
        for request in invalid_requests:
            with pytest.raises(UploadCompletionPartsInvalid):
                await service.complete(
                    principal=seeded.principal.context,
                    session_id=seeded.session_id,
                    request=request,
                )

        listed_mismatches = (
            UploadedPart(
                seeded.parts[0].part_number,
                seeded.parts[0].size_bytes + 1,
                seeded.parts[0].etag,
                seeded.parts[0].checksum_sha256_b64,
            ),
            UploadedPart(
                seeded.parts[0].part_number,
                seeded.parts[0].size_bytes,
                '"wrong-etag"',
                seeded.parts[0].checksum_sha256_b64,
            ),
            UploadedPart(
                seeded.parts[0].part_number,
                seeded.parts[0].size_bytes,
                seeded.parts[0].etag,
                _checksum(b"wrong-listed-checksum"),
            ),
        )
        for mismatched_part in listed_mismatches:
            store.listed_parts = (mismatched_part, seeded.parts[1])
            with pytest.raises(UploadCompletionVerificationFailed):
                await service.complete(
                    principal=seeded.principal.context,
                    session_id=seeded.session_id,
                    request=valid,
                )

        store.listed_parts = seeded.parts
        async with session_factory.begin() as database:
            await database.execute(
                update(UploadPart)
                .where(
                    UploadPart.upload_session_id == seeded.session_id,
                    UploadPart.part_number == 1,
                )
                .values(expected_checksum_sha256=_checksum(b"wrong-database-checksum"))
            )
        with pytest.raises(UploadCompletionPartsInvalid):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=valid,
            )
        assert store.complete_calls == 0
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ACTIVE.value
            assert upload_session.reserved_bytes == len(seeded.content)
            assert tenant.reserved_storage_bytes == len(seeded.content)
            assert tenant.used_storage_bytes == 0
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_expired_active_completion_stops_before_object_store_calls() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )

    try:
        async with session_factory.begin() as database:
            await database.execute(
                update(UploadSession)
                .where(UploadSession.id == seeded.session_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        with pytest.raises(UploadSessionExpired):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )

        assert store.list_calls == 0
        assert store.complete_calls == 0
        assert store.head_calls == 0
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.ACTIVE.value
            assert upload_session.reserved_bytes == len(seeded.content)
            assert tenant.reserved_storage_bytes == len(seeded.content)
            assert tenant.used_storage_bytes == 0
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_missing_active_multipart_fails_session_and_releases_quota_once() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    store.multipart_exists = False
    service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    request = _completion_request(seeded.parts)

    try:
        with pytest.raises(MultipartUploadNotFound):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=request,
            )

        assert store.list_calls == 1
        assert store.complete_calls == 0
        assert store.head_calls == 0
        assert store.delete_calls == 0
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.last_error_code == "multipart_upload_not_found"
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == 0

        with pytest.raises(UploadSessionNotActive):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=request,
            )
        assert store.list_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mismatch", "expected_delete_calls"),
    [
        ("size", 1),
        ("etag", 1),
        ("checksum", 1),
        ("missing-checksum", 1),
        ("contract", 0),
        ("session", 0),
        ("version", 0),
        ("declared-size", 0),
    ],
)
async def test_invalid_completed_head_releases_quota_and_deletes_only_owned_objects(
    mismatch: str,
    expected_delete_calls: int,
) -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    if mismatch == "size":
        store.head_size_bytes += 1
    elif mismatch == "etag":
        store.head_etag = '"wrong-head-etag"'
    elif mismatch == "checksum":
        store.head_checksum = "wrong-head-checksum"
    elif mismatch == "missing-checksum":
        store.head_checksum = None
    elif mismatch == "contract":
        store.metadata["contract"] = "wrong-contract"
    elif mismatch == "session":
        store.metadata["upload-session-id"] = str(uuid4())
    elif mismatch == "version":
        store.metadata["version-id"] = str(uuid4())
    elif mismatch == "declared-size":
        store.metadata["declared-size"] = str(len(seeded.content) + 1)
    service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )

    try:
        with pytest.raises(UploadCompletionVerificationFailed):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )

        assert store.delete_calls == expected_delete_calls
        assert store.object_exists is (expected_delete_calls == 0)
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            version_count = await database.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.upload_session_id == seeded.session_id)
            )
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.last_error_code == "upload_completion_verification_failed"
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == 0
            assert version_count == 0
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_completion_recovers_after_object_completion_before_database_finalization() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    crashing_service = CrashBeforeFinalizationService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    recovery_service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    request = _completion_request(seeded.parts)

    try:
        with pytest.raises(ConnectionError, match="post-object completion"):
            await crashing_service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=request,
            )
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            assert upload_session is not None
            assert upload_session.status == UploadSessionStatus.COMPLETING.value
            assert upload_session.document_version_id is None

        recovered = await recovery_service.complete(
            principal=seeded.principal.context,
            session_id=seeded.session_id,
            request=request,
        )
        replayed = await recovery_service.complete(
            principal=seeded.principal.context,
            session_id=seeded.session_id,
            request=request,
        )

        assert recovered.version_id == seeded.pending_version_id
        assert replayed.version_id == seeded.pending_version_id
        assert replayed.replayed is True
        assert store.complete_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            versions = (
                await database.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.upload_session_id == seeded.session_id
                    )
                )
            ).all()
            assert upload_session is not None and tenant is not None
            assert len(versions) == 1
            assert upload_session.document_version_id == seeded.pending_version_id
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == len(seeded.content)
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_completion_recovers_finalization_commit_acknowledgement_loss() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    faulting_factory = CommitAcknowledgementLostSessionFactory(
        session_factory,
        fail_on_begin=2,
    )
    service = UploadSessionService(
        session_factory=faulting_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )

    try:
        result = await service.complete(
            principal=seeded.principal.context,
            session_id=seeded.session_id,
            request=_completion_request(seeded.parts),
        )

        assert result.version_id == seeded.pending_version_id
        assert result.replayed is False
        assert store.complete_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            versions = (
                await database.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.upload_session_id == seeded.session_id
                    )
                )
            ).all()
            assert upload_session is not None and tenant is not None
            assert len(versions) == 1
            assert upload_session.document_version_id == seeded.pending_version_id
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == len(seeded.content)
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_invalid_completed_pdf_releases_quota_and_deletes_owned_object() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"not-a-pdf")
    store = CompletionObjectStore(seeded)
    service = UploadSessionService(
        session_factory=session_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )

    try:
        with pytest.raises(DocumentEnvelopeViolation) as exc_info:
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )
        assert exc_info.value.code == "document_pdf_signature_invalid"
        assert store.delete_calls == 1
        assert store.object_exists is False
        with pytest.raises(UploadSessionNotActive):
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )
        assert store.delete_calls == 1
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            version = await database.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.upload_session_id == seeded.session_id
                )
            )
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.last_error_code == "document_pdf_signature_invalid"
            assert upload_session.reserved_bytes == 0
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == 0
            assert version is None
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()


@pytest.mark.integration
async def test_invalid_completion_deletes_owned_object_after_failure_commit_ack_loss() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    seeded = await _seed_upload(session_factory, content=b"not-a-pdf")
    store = CompletionObjectStore(seeded)
    faulting_factory = CommitAcknowledgementLostSessionFactory(
        session_factory,
        fail_on_begin=2,
    )
    service = UploadSessionService(
        session_factory=faulting_factory,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )

    try:
        with pytest.raises(DocumentEnvelopeViolation) as exc_info:
            await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )

        assert exc_info.value.code == "document_pdf_signature_invalid"
        assert faulting_factory.begin_calls == 2
        assert store.delete_calls == 1
        assert store.object_exists is False
        async with session_factory() as database:
            upload_session = await database.get(UploadSession, seeded.session_id)
            tenant = await database.get(Tenant, seeded.principal.tenant_id)
            assert upload_session is not None and tenant is not None
            assert upload_session.status == UploadSessionStatus.FAILED.value
            assert upload_session.last_error_code == "document_pdf_signature_invalid"
            assert upload_session.reserved_bytes == 0
            assert upload_session.document_version_id is None
            assert tenant.reserved_storage_bytes == 0
            assert tenant.used_storage_bytes == 0
    finally:
        await _cleanup_seeded(session_factory, seeded)
        await engine.dispose()
