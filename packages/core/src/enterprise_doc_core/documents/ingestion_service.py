from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.documents.ingestion import (
    DocumentParseViolation,
    EmbeddingProvider,
    ParsedChunk,
    chunk_sections,
    parse_document_bytes,
)
from enterprise_doc_core.documents.models import (
    DEFAULT_EMBEDDING_DIMENSION,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_core.object_store import MultipartObjectStore, ObjectStoreError


class DocumentIngestionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IngestionVersions:
    parser: int = 1
    chunker: int = 1
    embedding: int = 1


@dataclass(frozen=True, slots=True)
class IngestionLimits:
    range_read_bytes: int = 1024 * 1024
    max_spool_memory_bytes: int = 8 * 1024 * 1024
    max_document_bytes: int = 256 * 1024 * 1024
    max_chunk_chars: int = 1200
    overlap_chars: int = 120

    def __post_init__(self) -> None:
        if self.range_read_bytes <= 0:
            raise ValueError("range_read_bytes must be positive")
        if self.max_spool_memory_bytes <= 0:
            raise ValueError("max_spool_memory_bytes must be positive")
        if self.max_document_bytes <= 0:
            raise ValueError("max_document_bytes must be positive")
        if self.max_chunk_chars <= 0:
            raise ValueError("max_chunk_chars must be positive")
        if self.overlap_chars < 0 or self.overlap_chars >= self.max_chunk_chars:
            raise ValueError("overlap_chars must be in [0, max_chunk_chars)")


async def spool_object(
    *,
    object_store: MultipartObjectStore,
    bucket: str,
    key: str,
    limits: IngestionLimits,
) -> SpooledTemporaryFile[bytes]:
    head = await object_store.head_object(bucket=bucket, key=key)
    if head.size_bytes > limits.max_document_bytes:
        raise DocumentIngestionError(
            "document_too_large",
            "document exceeds the ingestion size limit",
            retryable=False,
        )
    spool = SpooledTemporaryFile(max_size=limits.max_spool_memory_bytes, mode="w+b")
    try:
        for start in range(0, head.size_bytes, limits.range_read_bytes):
            end = min(start + limits.range_read_bytes, head.size_bytes) - 1
            spool.write(
                await object_store.get_range(
                    bucket=bucket,
                    key=key,
                    start=start,
                    end_inclusive=end,
                )
            )
        if spool.tell() != head.size_bytes:
            raise DocumentIngestionError(
                "object_size_mismatch",
                "object size changed during ingestion",
                retryable=True,
            )
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise


class DocumentIngestionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: MultipartObjectStore,
        documents_bucket: str,
        embedding_provider: EmbeddingProvider,
        embedding_model: str = "hash",
        embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        versions: IngestionVersions | None = None,
        limits: IngestionLimits | None = None,
    ) -> None:
        if embedding_dimension != DEFAULT_EMBEDDING_DIMENSION:
            raise ValueError(
                "current storage contract requires "
                f"{DEFAULT_EMBEDDING_DIMENSION}-dimensional embeddings"
            )
        self.session_factory = session_factory
        self.object_store = object_store
        self.documents_bucket = documents_bucket
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.versions = versions or IngestionVersions()
        self.limits = limits or IngestionLimits()

    async def __call__(self, claim: ClaimedJob) -> None:
        document_version_id = self._document_version_id(claim)
        generation_id, version, resume_stage, already_complete = await self._start_generation(
            tenant_id=claim.tenant_id,
            document_version_id=document_version_id,
        )
        if already_complete:
            return
        try:
            if resume_stage is DocumentIngestionStage.EMBED:
                chunks = await self._load_persisted_chunks(
                    tenant_id=claim.tenant_id,
                    document_version_id=document_version_id,
                    generation_id=generation_id,
                )
            else:
                await self._checkpoint(generation_id, DocumentIngestionStage.DOWNLOAD_SPOOL)
                spool = await spool_object(
                    object_store=self.object_store,
                    bucket=self.documents_bucket,
                    key=version.object_key,
                    limits=self.limits,
                )
                try:
                    data = spool.read()
                finally:
                    spool.close()

                await self._checkpoint(generation_id, DocumentIngestionStage.PARSE)
                sections = parse_document_bytes(
                    data,
                    extension=Path(version.original_filename).suffix,
                )
                await self._checkpoint(generation_id, DocumentIngestionStage.CHUNK)
                chunks = chunk_sections(
                    sections,
                    max_chars=self.limits.max_chunk_chars,
                    overlap_chars=self.limits.overlap_chars,
                )
                await self._persist_chunks(
                    tenant_id=claim.tenant_id,
                    document_version_id=document_version_id,
                    generation_id=generation_id,
                    chunks=chunks,
                )
            embeddings = await self.embedding_provider.embed(tuple(chunk.text for chunk in chunks))
            self._validate_embeddings(chunks, embeddings)
            await self._commit_embeddings_and_activate(
                tenant_id=claim.tenant_id,
                document_version_id=document_version_id,
                generation_id=generation_id,
                chunks=chunks,
                embeddings=embeddings,
            )
        except DocumentParseViolation as error:
            await self._mark_failed(
                document_version_id=document_version_id,
                generation_id=generation_id,
                code=error.code,
                message=error.message,
                deterministic=True,
            )
            raise DocumentIngestionError(error.code, error.message, retryable=False) from error
        except DocumentIngestionError as error:
            await self._mark_failed(
                document_version_id=document_version_id,
                generation_id=generation_id,
                code=error.code,
                message=error.message,
                deterministic=not error.retryable,
            )
            raise
        except ObjectStoreError as error:
            await self._mark_failed(
                document_version_id=document_version_id,
                generation_id=generation_id,
                code="object_store_unavailable",
                message="object store operation failed",
                deterministic=False,
            )
            raise DocumentIngestionError(
                "object_store_unavailable",
                "object store operation failed",
                retryable=True,
            ) from error
        except Exception as error:
            await self._mark_failed(
                document_version_id=document_version_id,
                generation_id=generation_id,
                code="ingestion_failed",
                message="document ingestion failed",
                deterministic=False,
            )
            raise DocumentIngestionError(
                "ingestion_failed", "document ingestion failed", retryable=True
            ) from error

    def _document_version_id(self, claim: ClaimedJob) -> UUID:
        raw = claim.payload.get("document_version_id")
        try:
            return UUID(str(raw))
        except (TypeError, ValueError, AttributeError) as exc:
            raise DocumentIngestionError(
                "job_payload_invalid",
                "document_version_id is required",
                retryable=False,
            ) from exc

    async def _start_generation(
        self, *, tenant_id: UUID, document_version_id: UUID
    ) -> tuple[UUID, DocumentVersion, DocumentIngestionStage, bool]:
        async with self.session_factory() as session, session.begin():
            version = await session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == document_version_id,
                    DocumentVersion.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if version is None:
                raise DocumentIngestionError(
                    "document_version_not_found",
                    "document version was not found",
                    retryable=False,
                )
            generation = await session.scalar(
                select(DocumentIngestionGeneration)
                .where(
                    DocumentIngestionGeneration.document_version_id == document_version_id,
                    DocumentIngestionGeneration.parser_version == self.versions.parser,
                    DocumentIngestionGeneration.chunker_version == self.versions.chunker,
                    DocumentIngestionGeneration.embedding_version == self.versions.embedding,
                )
                .with_for_update()
            )
            if generation is None:
                generation = DocumentIngestionGeneration(
                    tenant_id=tenant_id,
                    document_version_id=document_version_id,
                    parser_version=self.versions.parser,
                    chunker_version=self.versions.chunker,
                    embedding_version=self.versions.embedding,
                    embedding_model=self.embedding_model,
                    embedding_dimension=self.embedding_dimension,
                    status=DocumentIngestionStatus.RUNNING.value,
                    stage=DocumentIngestionStage.DOWNLOAD_SPOOL.value,
                    started_at=func.now(),
                )
                session.add(generation)
                await session.flush()
            elif (
                generation.status == DocumentIngestionStatus.SUCCEEDED.value
                and generation.stage == DocumentIngestionStage.READY.value
                and version.status == DocumentVersionStatus.READY.value
            ):
                return generation.id, version, DocumentIngestionStage.READY, True
            else:
                resume_stage = DocumentIngestionStage(generation.stage)
                if resume_stage is not DocumentIngestionStage.EMBED:
                    await session.execute(
                        delete(DocumentChunk).where(DocumentChunk.generation_id == generation.id)
                    )
                    generation.stage = DocumentIngestionStage.DOWNLOAD_SPOOL.value
                    generation.chunk_count = 0
                    generation.embedded_count = 0
                    resume_stage = DocumentIngestionStage.DOWNLOAD_SPOOL
                generation.status = DocumentIngestionStatus.RUNNING.value
                generation.error_code = None
                generation.error_message = None
                generation.finished_at = None
                generation.started_at = func.now()
                return generation.id, version, resume_stage, False
            return generation.id, version, DocumentIngestionStage.DOWNLOAD_SPOOL, False

    async def _checkpoint(self, generation_id: UUID, stage: DocumentIngestionStage) -> None:
        async with self.session_factory() as session, session.begin():
            generation = await session.scalar(
                select(DocumentIngestionGeneration)
                .where(DocumentIngestionGeneration.id == generation_id)
                .with_for_update()
            )
            if generation is None:
                raise DocumentIngestionError(
                    "generation_not_found", "ingestion generation was not found", retryable=False
                )
            generation.status = DocumentIngestionStatus.RUNNING.value
            generation.stage = stage.value

    def _validate_embeddings(
        self,
        chunks: tuple[ParsedChunk, ...],
        embeddings: tuple[tuple[float, ...], ...],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise DocumentIngestionError(
                "embedding_count_mismatch",
                "embedding provider returned the wrong number of vectors",
                retryable=True,
            )
        if any(len(vector) != self.embedding_dimension for vector in embeddings):
            raise DocumentIngestionError(
                "embedding_dimension_mismatch",
                "embedding provider returned a vector with the wrong dimension",
                retryable=False,
            )

    async def _persist_chunks(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        generation_id: UUID,
        chunks: tuple[ParsedChunk, ...],
    ) -> None:
        async with self.session_factory() as session, session.begin():
            generation = await session.scalar(
                select(DocumentIngestionGeneration)
                .where(
                    DocumentIngestionGeneration.id == generation_id,
                    DocumentIngestionGeneration.tenant_id == tenant_id,
                    DocumentIngestionGeneration.document_version_id == document_version_id,
                )
                .with_for_update()
            )
            if generation is None:
                raise DocumentIngestionError(
                    "ingestion_target_missing", "ingestion target was not found", retryable=False
                )
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.generation_id == generation_id)
            )
            for chunk in chunks:
                search_text = f"{chunk.heading or ''} {chunk.text}".strip()
                session.add(
                    DocumentChunk(
                        tenant_id=tenant_id,
                        document_version_id=document_version_id,
                        generation_id=generation_id,
                        chunk_index=chunk.chunk_index,
                        heading=chunk.heading,
                        page_number=chunk.page_number,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.end_offset,
                        normalized_text=chunk.text,
                        content_sha256=chunk.content_sha256,
                        search_vector=func.to_tsvector("simple", search_text),
                        embedding=None,
                    )
                )
            await session.flush()
            generation.stage = DocumentIngestionStage.EMBED.value
            generation.status = DocumentIngestionStatus.RUNNING.value
            generation.chunk_count = len(chunks)
            generation.embedded_count = 0

    async def _load_persisted_chunks(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        generation_id: UUID,
    ) -> tuple[ParsedChunk, ...]:
        async with self.session_factory() as session:
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.id == generation_id,
                    DocumentIngestionGeneration.tenant_id == tenant_id,
                    DocumentIngestionGeneration.document_version_id == document_version_id,
                )
            )
            rows = (
                await session.scalars(
                    select(DocumentChunk)
                    .where(
                        DocumentChunk.generation_id == generation_id,
                        DocumentChunk.tenant_id == tenant_id,
                        DocumentChunk.document_version_id == document_version_id,
                    )
                    .order_by(DocumentChunk.chunk_index)
                )
            ).all()
        if generation is None or generation.stage != DocumentIngestionStage.EMBED.value:
            raise DocumentIngestionError(
                "ingestion_checkpoint_invalid",
                "persisted ingestion checkpoint is invalid",
                retryable=False,
            )
        expected_indexes = list(range(generation.chunk_count))
        if (
            [row.chunk_index for row in rows] != expected_indexes
            or generation.embedded_count != 0
            or any(row.embedding is not None for row in rows)
            or any(
                hashlib.sha256(row.normalized_text.encode("utf-8")).hexdigest()
                != row.content_sha256
                for row in rows
            )
        ):
            raise DocumentIngestionError(
                "ingestion_checkpoint_invalid",
                "persisted ingestion checkpoint is invalid",
                retryable=False,
            )
        return tuple(
            ParsedChunk(
                chunk_index=row.chunk_index,
                text=row.normalized_text,
                start_offset=row.start_offset,
                end_offset=row.end_offset,
                content_sha256=row.content_sha256,
                page_number=row.page_number,
                heading=row.heading,
            )
            for row in rows
        )

    async def _commit_embeddings_and_activate(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        generation_id: UUID,
        chunks: tuple[ParsedChunk, ...],
        embeddings: tuple[tuple[float, ...], ...],
    ) -> None:
        async with self.session_factory() as session, session.begin():
            version = await session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == document_version_id,
                    DocumentVersion.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            generation = await session.scalar(
                select(DocumentIngestionGeneration)
                .where(
                    DocumentIngestionGeneration.id == generation_id,
                    DocumentIngestionGeneration.tenant_id == tenant_id,
                    DocumentIngestionGeneration.document_version_id == document_version_id,
                )
                .with_for_update()
            )
            rows = (
                await session.scalars(
                    select(DocumentChunk)
                    .where(
                        DocumentChunk.generation_id == generation_id,
                        DocumentChunk.tenant_id == tenant_id,
                        DocumentChunk.document_version_id == document_version_id,
                    )
                    .order_by(DocumentChunk.chunk_index)
                    .with_for_update()
                )
            ).all()
            if version is None or generation is None:
                raise DocumentIngestionError(
                    "ingestion_target_missing", "ingestion target was not found", retryable=False
                )
            if (
                generation.stage != DocumentIngestionStage.EMBED.value
                or generation.chunk_count != len(chunks)
                or len(rows) != len(chunks)
                or any(
                    row.chunk_index != chunk.chunk_index
                    for row, chunk in zip(rows, chunks, strict=True)
                )
            ):
                raise DocumentIngestionError(
                    "ingestion_checkpoint_invalid",
                    "persisted ingestion checkpoint is invalid",
                    retryable=False,
                )
            generation.stage = DocumentIngestionStage.INDEX.value
            for row, embedding in zip(rows, embeddings, strict=True):
                row.embedding = list(embedding)
            await session.flush()
            await session.execute(
                update(DocumentIngestionGeneration)
                .where(
                    DocumentIngestionGeneration.tenant_id == tenant_id,
                    DocumentIngestionGeneration.document_version_id == document_version_id,
                    DocumentIngestionGeneration.id != generation_id,
                    DocumentIngestionGeneration.active.is_(True),
                )
                .values(active=False)
            )
            generation.stage = DocumentIngestionStage.READY.value
            generation.status = DocumentIngestionStatus.SUCCEEDED.value
            generation.chunk_count = len(chunks)
            generation.embedded_count = len(embeddings)
            generation.active = True
            generation.error_code = None
            generation.error_message = None
            generation.finished_at = func.now()
            version.status = DocumentVersionStatus.READY.value

    async def _mark_failed(
        self,
        *,
        document_version_id: UUID,
        generation_id: UUID,
        code: str,
        message: str,
        deterministic: bool,
    ) -> None:
        async with self.session_factory() as session, session.begin():
            generation = await session.scalar(
                select(DocumentIngestionGeneration)
                .where(DocumentIngestionGeneration.id == generation_id)
                .with_for_update()
            )
            version = await session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.id == document_version_id)
                .with_for_update()
            )
            if generation is not None:
                generation.status = DocumentIngestionStatus.FAILED.value
                generation.error_code = code[:100]
                generation.error_message = message[:1000]
                generation.finished_at = func.now()
            if (
                deterministic
                and version is not None
                and version.status != DocumentVersionStatus.READY.value
            ):
                version.status = DocumentVersionStatus.FAILED.value
