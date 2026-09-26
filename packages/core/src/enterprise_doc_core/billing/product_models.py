from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProductQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_quotas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", "metric", name="uq_product_quotas_tenant_metric_id"),
        UniqueConstraint(
            "tenant_id", "entitlement_id", "metric", name="uq_product_quotas_period_metric"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("metric IN ('agent_task', 'document_bytes')", name="metric_valid"),
        CheckConstraint(
            "unit_limit >= 0 AND units_used >= 0 AND units_reserved >= 0", name="units_non_negative"
        ),
        CheckConstraint("units_used <= unit_limit - units_reserved", name="within_limit"),
    )

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    entitlement_id: Mapped[UUID] = mapped_column(nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    units_used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    units_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class ProductUsageReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_usage_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_product_usage_reservations_tenant_id"),
        UniqueConstraint("tenant_id", "metric", "operation_id", name="uq_product_usage_operation"),
        ForeignKeyConstraint(
            ["tenant_id", "quota_id", "metric"],
            ["product_quotas.tenant_id", "product_quotas.id", "product_quotas.metric"],
            ondelete="CASCADE",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("metric != 'agent_task' OR quantity = 1", name="one_task"),
        CheckConstraint("state IN ('reserved', 'consumed', 'released')", name="state_valid"),
        Index("ix_product_usage_quota_state", "tenant_id", "quota_id", "state"),
    )

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    quota_id: Mapped[UUID] = mapped_column(nullable=False)
    operation_id: Mapped[UUID] = mapped_column(nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="reserved")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductUsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reservation_id", name="uq_product_usage_terminal_event"),
        ForeignKeyConstraint(
            ["tenant_id", "reservation_id"],
            ["product_usage_reservations.tenant_id", "product_usage_reservations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("event_type IN ('consume', 'release')", name="event_type_valid"),
        Index("ix_product_usage_events_tenant_time", "tenant_id", "occurred_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    reservation_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
