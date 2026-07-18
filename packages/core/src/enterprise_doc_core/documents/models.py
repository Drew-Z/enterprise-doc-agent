from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentVersionStatus(StrEnum):
    UPLOADED = "uploaded"
    READY = "ready"
    FAILED = "failed"


class DocumentIngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DocumentIngestionStage(StrEnum):
    DOWNLOAD_SPOOL = "download_spool"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    READY = "ready"


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_tenant_id_created_at", "tenant_id", "created_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "upload_session_id",
            name="uq_document_versions_upload_session_id",
        ),
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_id_version_number",
        ),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("status IN ('uploaded', 'ready', 'failed')", name="status_valid"),
        Index(
            "ix_document_versions_tenant_id_status_created_at",
            "tenant_id",
            "status",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    detected_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transport_checksum_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )


DEFAULT_EMBEDDING_DIMENSION = 8


class DocumentIngestionGeneration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_ingestion_generations"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "parser_version",
            "chunker_version",
            "embedding_version",
            name="uq_document_ingestion_generations_version_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        CheckConstraint(
            "stage IN ('download_spool', 'parse', 'chunk', 'embed', 'index', 'ready')",
            name="stage_valid",
        ),
        CheckConstraint("parser_version > 0", name="parser_version_positive"),
        CheckConstraint("chunker_version > 0", name="chunker_version_positive"),
        CheckConstraint("embedding_version > 0", name="embedding_version_positive"),
        CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        CheckConstraint("embedded_count >= 0", name="embedded_count_non_negative"),
        Index(
            "ix_document_ingestion_generations_tenant_version_status",
            "tenant_id",
            "document_version_id",
            "status",
        ),
        Index(
            "uq_document_ingestion_generations_active_version",
            "tenant_id",
            "document_version_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    chunker_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    embedding_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="hash")
    embedding_dimension: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text(str(DEFAULT_EMBEDDING_DIMENSION))
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=DocumentIngestionStatus.PENDING.value
    )
    stage: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=DocumentIngestionStage.DOWNLOAD_SPOOL.value
    )
    stage_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    embedded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "chunk_index", name="uq_document_chunks_generation_index"
        ),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("start_offset >= 0 AND end_offset >= start_offset", name="offsets_valid"),
        Index(
            "ix_document_chunks_tenant_version_generation",
            "tenant_id",
            "document_version_id",
            "generation_id",
        ),
        Index("ix_document_chunks_generation_chunk_index", "generation_id", "chunk_index"),
        Index(
            "ix_document_chunks_fts",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_ingestion_generations.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(DEFAULT_EMBEDDING_DIMENSION), nullable=False
    )
