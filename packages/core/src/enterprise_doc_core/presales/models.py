from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PresalesPacket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "presales_packets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_presales_packets_tenant_id"),
        UniqueConstraint(
            "tenant_id", "actor_id", "idempotency_key", name="uq_presales_packets_create_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_presales_packets_actor_created", "tenant_id", "actor_id", "created_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class PresalesRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "presales_rows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_presales_rows_tenant_id"),
        UniqueConstraint("packet_id", "position", name="uq_presales_rows_position"),
        UniqueConstraint("packet_id", "requirement_key", name="uq_presales_rows_key"),
        ForeignKeyConstraint(
            ["tenant_id", "packet_id"],
            ["presales_packets.tenant_id", "presales_packets.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("revision >= 0", name="presales_row_revision_valid"),
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    packet_id: Mapped[UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer)
    requirement_key: Mapped[str] = mapped_column(String(40))
    requirement_text: Mapped[str] = mapped_column(Text)
    source_location: Mapped[str] = mapped_column(String(300))
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class PresalesAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "presales_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "row_id"],
            ["presales_rows.tenant_id", "presales_rows.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("row_id", "number", name="uq_presales_attempts_number"),
        UniqueConstraint("row_id", "idempotency_key", name="uq_presales_attempts_key"),
        CheckConstraint(
            "state IN ('running', 'succeeded', 'failed', 'expired')",
            name="presales_attempt_state_valid",
        ),
        CheckConstraint(
            "number BETWEEN 1 AND 3 AND provider_request_count BETWEEN 0 AND 1",
            name="presales_attempt_limits",
        ),
        Index("ix_presales_attempts_tenant_started", "tenant_id", "created_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    row_id: Mapped[UUID] = mapped_column(nullable=False)
    number: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # NULL means dispatch was prepared but its outcome was not durably observed.
    provider_request_count: Mapped[int | None] = mapped_column(
        Integer, default=0, server_default="0", nullable=True
    )
    provenance: Mapped[dict[str, str | None]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    usage: Mapped[dict[str, int | None] | None] = mapped_column(JSONB, nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PresalesReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "presales_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "row_id"],
            ["presales_rows.tenant_id", "presales_rows.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("row_id", "revision", name="uq_presales_reviews_revision"),
        UniqueConstraint("row_id", "idempotency_key", name="uq_presales_reviews_key"),
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    row_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
