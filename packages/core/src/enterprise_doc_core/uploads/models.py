from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

UPLOAD_PART_OBSERVATION_VERSION_SEQUENCE = Sequence(
    "upload_part_observation_version_seq",
    metadata=Base.metadata,
)


class UploadSessionStatus(StrEnum):
    INITIALIZING = "initializing"
    ACTIVE = "active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"
    FAILED = "failed"


class UploadSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_upload_sessions_tenant_id_idempotency_key",
        ),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("part_size_bytes > 0", name="part_size_positive"),
        CheckConstraint(
            "expected_part_count BETWEEN 1 AND 10000",
            name="expected_part_count_range",
        ),
        CheckConstraint("reserved_bytes >= 0", name="reserved_bytes_non_negative"),
        CheckConstraint(
            "status IN ('initializing', 'active', 'completing', 'completed', "
            "'aborted', 'expired', 'failed')",
            name="status_valid",
        ),
        Index(
            "ix_upload_sessions_tenant_id_status_expires_at",
            "tenant_id",
            "status",
            "expires_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pending_document_id: Mapped[UUID] = mapped_column(nullable=False)
    pending_version_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    object_store_upload_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    part_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completion_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aborted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class UploadPart(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "upload_parts"
    __table_args__ = (
        UniqueConstraint(
            "upload_session_id",
            "part_number",
            name="uq_upload_parts_upload_session_id_part_number",
        ),
        CheckConstraint("part_number BETWEEN 1 AND 10000", name="part_number_range"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="size_bytes_positive"),
        Index("ix_upload_parts_tenant_id_upload_session_id", "tenant_id", "upload_session_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observation_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
