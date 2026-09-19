from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TenantEntitlement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenant_entitlements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_entitlements_tenant_id"),
        UniqueConstraint("tenant_id", "version", name="uq_tenant_entitlements_version"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("period_end > period_start", name="valid_period"),
        CheckConstraint(
            "provider_request_limit IS NULL OR provider_request_limit >= 0",
            name="provider_request_limit_non_negative",
        ),
        CheckConstraint(
            "provider_requests_used >= 0 AND provider_requests_reserved >= 0",
            name="provider_request_counters_non_negative",
        ),
        Index("ix_tenant_entitlements_current", "tenant_id", "period_start", "period_end"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_request_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_requests_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    provider_requests_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class UsageReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_reservations_tenant_id"),
        UniqueConstraint("tenant_id", "operation_id", name="uq_usage_reservations_operation"),
        ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("metric = 'provider_request'", name="provider_request_metric"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint(
            "state IN ('reserved', 'consumed', 'released')", name="reservation_state_valid"
        ),
        Index("ix_usage_reservations_expiry", "tenant_id", "entitlement_id", "state", "expires_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    entitlement_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    metric: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="provider_request"
    )
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="reserved")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_events_tenant_id"),
        UniqueConstraint(
            "tenant_id", "operation_id", "event_type", name="uq_usage_events_operation_type"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "entitlement_id"],
            ["tenant_entitlements.tenant_id", "tenant_entitlements.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reservation_id"],
            ["usage_reservations.tenant_id", "usage_reservations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("event_type IN ('consume', 'release')", name="usage_event_type_valid"),
        CheckConstraint("quantity > 0", name="usage_event_quantity_positive"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="usage_event_input_tokens_non_negative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="usage_event_output_tokens_non_negative",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="usage_event_total_tokens_non_negative",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="usage_event_cost_non_negative",
        ),
        Index("ix_usage_events_tenant_time", "tenant_id", "occurred_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    entitlement_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reservation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    metric: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="provider_request"
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
