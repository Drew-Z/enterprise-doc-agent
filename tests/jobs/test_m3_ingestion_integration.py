from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
    IngestionVersions,
)
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_core.object_store.models import ObjectHead
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus


class FakeObjectStore:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.head_calls = 0
        self.range_calls: list[tuple[int, int]] = []

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        self.head_calls += 1
        return ObjectHead(len(self.content), "etag", None, "text/plain", {})

    async def get_range(self, *, bucket: str, key: str, start: int, end_inclusive: int) -> bytes:
        self.range_calls.append((start, end_inclusive))
        return self.content[start : end_inclusive + 1]


class FailOnceEmbeddingProvider:
    def __init__(self) -> None:
        self.delegate = HashEmbeddingProvider()
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        snapshot = tuple(texts)
        self.calls.append(snapshot)
        if len(self.calls) == 1:
            raise RuntimeError("injected embedding failure")
        return await self.delegate.embed(snapshot)


async def _seed_uploaded_document(
    session_factory,
    *,
    content: bytes,
) -> tuple[UUID, UUID, UUID, ClaimedJob]:
    tenant_id = uuid4()
    actor_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    upload_id = uuid4()
    suffix = uuid4().hex
    object_key = f"{tenant_id}/documents/{version_id}/contract.txt"
    sha256 = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"M3 resume tenant {suffix}",
                slug=f"m3-resume-{suffix}",
                quota_bytes=1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"m3-resume-{suffix}@example.test"))
        await session.flush()
        session.add(
            UploadSession(
                id=upload_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                pending_document_id=document_id,
                pending_version_id=version_id,
                status=UploadSessionStatus.COMPLETED.value,
                idempotency_key=f"upload-resume:{suffix}",
                request_fingerprint=sha256,
                object_key=object_key,
                original_filename="contract.txt",
                extension=".txt",
                declared_media_type="text/plain",
                size_bytes=len(content),
                declared_sha256=sha256,
                part_size_bytes=len(content),
                expected_part_count=1,
                reserved_bytes=0,
                expires_at=now + timedelta(hours=1),
                completed_at=now,
            )
        )
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                created_by=actor_id,
                title="Resume Contract",
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                tenant_id=tenant_id,
                document_id=document_id,
                upload_session_id=upload_id,
                version_number=1,
                status=DocumentVersionStatus.UPLOADED.value,
                object_key=object_key,
                original_filename="contract.txt",
                declared_media_type="text/plain",
                detected_media_type="text/plain",
                size_bytes=len(content),
                declared_sha256=sha256,
                created_by=actor_id,
            )
        )
        await session.flush()
        upload = await session.get(UploadSession, upload_id)
        assert upload is not None
        upload.document_version_id = version_id
    claim = ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=tenant_id,
        actor_id=actor_id,
        worker_id="worker-test",
        lease_token=uuid4(),
        fencing_token=1,
        job_type="document.ingest",
        payload={"document_version_id": str(version_id)},
    )
    return tenant_id, actor_id, version_id, claim


@pytest.mark.integration
async def test_embedding_retry_resumes_without_downloading_the_object_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = b"# Delivery\nAcceptance requires a signed delivery certificate.\n"
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, _, version_id, claim = await _seed_uploaded_document(
        session_factory,
        content=content,
    )
    object_store = FakeObjectStore(content)
    provider = FailOnceEmbeddingProvider()
    service = DocumentIngestionService(
        session_factory=session_factory,
        object_store=object_store,  # type: ignore[arg-type]
        documents_bucket="documents",
        embedding_provider=provider,
    )
    try:
        with pytest.raises(DocumentIngestionError) as caught:
            await service(claim)
        assert caught.value.code == "ingestion_failed"
        assert caught.value.retryable is True
        unhandled_records = [
            record for record in caplog.records if record.msg == "document_ingestion_unhandled"
        ]
        assert unhandled_records
        assert unhandled_records[-1].event_data == {
            "error_type": "RuntimeError",
            "stage": "embed",
        }
        assert "injected embedding failure" not in caplog.text

        async with session_factory() as session:
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            persisted_chunks = (
                await session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_version_id == version_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            ).all()
        assert generation is not None
        assert generation.status == DocumentIngestionStatus.FAILED.value
        assert generation.stage == DocumentIngestionStage.EMBED.value
        assert generation.chunk_count == len(persisted_chunks) == 1
        assert generation.embedded_count == 0
        assert all(chunk.embedding is None for chunk in persisted_chunks)
        persisted_ids = [chunk.id for chunk in persisted_chunks]
        download_counts = (object_store.head_calls, len(object_store.range_calls))

        await service(claim)

        async with session_factory() as session:
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            resumed_chunks = (
                await session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_version_id == version_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            ).all()
        assert generation is not None
        assert generation.status == DocumentIngestionStatus.SUCCEEDED.value
        assert generation.stage == DocumentIngestionStage.READY.value
        assert generation.active is True
        assert generation.embedded_count == generation.chunk_count == 1
        assert [chunk.id for chunk in resumed_chunks] == persisted_ids
        assert all(chunk.embedding is not None for chunk in resumed_chunks)
        assert (object_store.head_calls, len(object_store.range_calls)) == download_counts
        assert provider.calls[0] == provider.calls[1]
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_invalid_embedding_checkpoint_is_rejected_without_redownload() -> None:
    content = b"# Delivery\nAcceptance requires a signed delivery certificate.\n"
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, _, version_id, claim = await _seed_uploaded_document(
        session_factory,
        content=content,
    )
    object_store = FakeObjectStore(content)
    provider = FailOnceEmbeddingProvider()
    service = DocumentIngestionService(
        session_factory=session_factory,
        object_store=object_store,  # type: ignore[arg-type]
        documents_bucket="documents",
        embedding_provider=provider,
    )
    try:
        with pytest.raises(DocumentIngestionError):
            await service(claim)
        async with session_factory.begin() as session:
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            assert generation is not None
            generation.chunk_count = 2
        download_counts = (object_store.head_calls, len(object_store.range_calls))

        with pytest.raises(DocumentIngestionError) as caught:
            await service(claim)
        assert caught.value.code == "ingestion_checkpoint_invalid"
        assert caught.value.retryable is False
        assert len(provider.calls) == 1
        assert (object_store.head_calls, len(object_store.range_calls)) == download_counts
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_document_ingestion_is_idempotent_and_hybrid_retrievable() -> None:
    content = b"# Payment Terms\nPayment is due within 30 days after acceptance.\n"
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id = uuid4()
    actor_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    upload_id = uuid4()
    suffix = uuid4().hex
    object_key = f"{tenant_id}/documents/{version_id}/contract.txt"
    sha256 = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC)
    try:
        async with session_factory.begin() as session:
            session.add(
                Tenant(
                    id=tenant_id,
                    name=f"M3 tenant {suffix}",
                    slug=f"m3-{suffix}",
                    quota_bytes=1024 * 1024,
                )
            )
            session.add(User(id=actor_id, email=f"m3-{suffix}@example.test"))

        async with session_factory.begin() as session:
            session.add(
                UploadSession(
                    id=upload_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    pending_document_id=document_id,
                    pending_version_id=version_id,
                    status=UploadSessionStatus.COMPLETED.value,
                    idempotency_key=f"upload:{suffix}",
                    request_fingerprint=sha256,
                    object_key=object_key,
                    original_filename="contract.txt",
                    extension=".txt",
                    declared_media_type="text/plain",
                    size_bytes=len(content),
                    declared_sha256=sha256,
                    part_size_bytes=len(content),
                    expected_part_count=1,
                    reserved_bytes=0,
                    expires_at=now + timedelta(hours=1),
                    completed_at=now,
                )
            )
            session.add(
                Document(id=document_id, tenant_id=tenant_id, created_by=actor_id, title="Contract")
            )
            await session.flush()
            session.add(
                DocumentVersion(
                    id=version_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    upload_session_id=upload_id,
                    version_number=1,
                    status=DocumentVersionStatus.UPLOADED.value,
                    object_key=object_key,
                    original_filename="contract.txt",
                    declared_media_type="text/plain",
                    detected_media_type="text/plain",
                    size_bytes=len(content),
                    declared_sha256=sha256,
                    created_by=actor_id,
                )
            )
            await session.flush()
            upload = await session.get(UploadSession, upload_id)
            assert upload is not None
            upload.document_version_id = version_id

        provider = HashEmbeddingProvider()
        service = DocumentIngestionService(
            session_factory=session_factory,
            object_store=FakeObjectStore(content),  # type: ignore[arg-type]
            documents_bucket="documents",
            embedding_provider=provider,
        )
        claim = ClaimedJob(
            job_id=uuid4(),
            attempt_id=uuid4(),
            attempt_number=1,
            tenant_id=tenant_id,
            actor_id=actor_id,
            worker_id="worker-test",
            lease_token=uuid4(),
            fencing_token=1,
            job_type="document.ingest",
            payload={"document_version_id": str(version_id)},
        )

        await service(claim)
        await service(claim)

        async with session_factory() as session:
            version = await session.get(DocumentVersion, version_id)
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            chunk_count = await session.scalar(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_version_id == version_id
                )
            )
        assert version is not None and version.status == DocumentVersionStatus.READY.value
        assert generation is not None and generation.active is True
        assert generation.chunk_count == chunk_count == 1

        retrieval = HybridRetrievalService(
            session_factory=session_factory,
            embedding_provider=provider,
            top_k=5,
        )
        decision = await retrieval.retrieve(
            tenant_id=tenant_id,
            document_version_id=version_id,
            query="payment 30 days",
        )

        assert decision.accepted is True
        assert decision.candidates[0].document_version_id == version_id
        assert "30 days" in decision.candidates[0].text
        assert (
            await retrieval.retrieve(
                tenant_id=uuid4(),
                document_version_id=version_id,
                query="payment 30 days",
            )
        ).accepted is False

        upgraded_service = DocumentIngestionService(
            session_factory=session_factory,
            object_store=FakeObjectStore(content),  # type: ignore[arg-type]
            documents_bucket="documents",
            embedding_provider=provider,
            versions=IngestionVersions(embedding=2),
        )
        await upgraded_service(claim)

        async with session_factory() as session:
            generation_count = await session.scalar(
                select(func.count(DocumentIngestionGeneration.id)).where(
                    DocumentIngestionGeneration.document_version_id == version_id
                )
            )
            active_count = await session.scalar(
                select(func.count(DocumentIngestionGeneration.id)).where(
                    DocumentIngestionGeneration.document_version_id == version_id,
                    DocumentIngestionGeneration.active.is_(True),
                )
            )
        assert generation_count == 2
        assert active_count == 1

        bad_content = b"\xff"
        bad_version_id = uuid4()
        bad_upload_id = uuid4()
        bad_object_key = f"{tenant_id}/documents/{bad_version_id}/broken.txt"
        bad_sha256 = hashlib.sha256(bad_content).hexdigest()
        async with session_factory.begin() as session:
            session.add(
                UploadSession(
                    id=bad_upload_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    pending_document_id=document_id,
                    pending_version_id=bad_version_id,
                    status=UploadSessionStatus.COMPLETED.value,
                    idempotency_key=f"upload-bad:{suffix}",
                    request_fingerprint=bad_sha256,
                    object_key=bad_object_key,
                    original_filename="broken.txt",
                    extension=".txt",
                    declared_media_type="text/plain",
                    size_bytes=1,
                    declared_sha256=bad_sha256,
                    part_size_bytes=1,
                    expected_part_count=1,
                    reserved_bytes=0,
                    expires_at=now + timedelta(hours=1),
                    completed_at=now,
                )
            )
            await session.flush()
            session.add(
                DocumentVersion(
                    id=bad_version_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    upload_session_id=bad_upload_id,
                    version_number=2,
                    status=DocumentVersionStatus.UPLOADED.value,
                    object_key=bad_object_key,
                    original_filename="broken.txt",
                    declared_media_type="text/plain",
                    detected_media_type="text/plain",
                    size_bytes=1,
                    declared_sha256=bad_sha256,
                    created_by=actor_id,
                )
            )
            await session.flush()
            bad_upload = await session.get(UploadSession, bad_upload_id)
            assert bad_upload is not None
            bad_upload.document_version_id = bad_version_id

        bad_service = DocumentIngestionService(
            session_factory=session_factory,
            object_store=FakeObjectStore(bad_content),  # type: ignore[arg-type]
            documents_bucket="documents",
            embedding_provider=provider,
        )
        bad_claim = ClaimedJob(
            job_id=uuid4(),
            attempt_id=uuid4(),
            attempt_number=1,
            tenant_id=tenant_id,
            actor_id=actor_id,
            worker_id="worker-test",
            lease_token=uuid4(),
            fencing_token=1,
            job_type="document.ingest",
            payload={"document_version_id": str(bad_version_id)},
        )
        with pytest.raises(DocumentIngestionError) as caught:
            await bad_service(bad_claim)
        assert caught.value.code == "text_decode_failed"
        assert caught.value.retryable is False

        async with session_factory() as session:
            bad_version = await session.get(DocumentVersion, bad_version_id)
            bad_generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.document_version_id == bad_version_id
                )
            )
        assert bad_version is not None and bad_version.status == DocumentVersionStatus.FAILED.value
        assert bad_generation is not None and bad_generation.error_code == "text_decode_failed"
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()
