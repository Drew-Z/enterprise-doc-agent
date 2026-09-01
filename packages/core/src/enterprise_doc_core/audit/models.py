from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Tenant-scoped append-only projection for governance activity."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index(
            "ix_audit_events_tenant_id_occurred_at_id",
            "tenant_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_audit_events_tenant_id_action_occurred_at",
            "tenant_id",
            "action",
            "occurred_at",
        ),
        Index(
            "ix_audit_events_tenant_id_resource_occurred_at",
            "tenant_id",
            "resource_type",
            "resource_id",
            "occurred_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, server_default=text("'{}'")
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class AuditRetentionPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-owned retention configuration; execution remains a separate gate."""

    __tablename__ = "audit_retention_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_audit_retention_policies_tenant_id"),
        CheckConstraint(
            "retention_days >= 30 AND retention_days <= 3650",
            name="audit_retention_days_valid",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("365"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AuditLegalHold(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-wide or resource-scoped preservation order for audit events."""

    __tablename__ = "audit_legal_holds"
    __table_args__ = (
        CheckConstraint(
            "(resource_type IS NULL AND resource_id IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name="audit_legal_hold_scope_valid",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > starts_at",
            name="audit_legal_hold_expiry_valid",
        ),
        Index(
            "ix_audit_legal_holds_tenant_id_released_at_expires_at",
            "tenant_id",
            "released_at",
            "expires_at",
        ),
        Index(
            "ix_audit_legal_holds_tenant_id_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    released_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AuditArchiveBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable receipt for an audit snapshot written to archive storage."""

    __tablename__ = "audit_archive_batches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "fingerprint",
            name="uq_audit_archive_batches_tenant_fingerprint",
        ),
        Index(
            "ix_audit_archive_batches_tenant_created_at",
            "tenant_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
