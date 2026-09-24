from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin


class ProviderDispatch(UUIDPrimaryKeyMixin, Base):
    """One committed dispatch intent per HTTP attempt, including uncertain results."""

    __tablename__ = "provider_dispatches"
    __table_args__ = (
        CheckConstraint("kind IN ('agent', 'document', 'query')", name="kind_valid"),
        CheckConstraint(
            "state IN ('dispatched', 'responded', 'http_error', 'timeout', "
            "'transport_error', 'cancelled', 'unknown')",
            name="state_valid",
        ),
        CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="tokens_nonnegative"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="cost_nonnegative"),
        Index("ix_provider_dispatches_tenant_time", "tenant_id", "started_at"),
        Index("ix_provider_dispatches_operation", "tenant_id", "kind", "operation_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    operation_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    channel_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    status_code: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    currency: Mapped[str | None] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
