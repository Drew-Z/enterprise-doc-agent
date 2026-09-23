from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin


class DemoWorkspace(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "demo_workspaces"
    __table_args__ = (
        CheckConstraint("attempts_used >= 0 AND attempts_used <= 6", name="attempts_bounded"),
        CheckConstraint("expires_at > created_at", name="expiry_order"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), unique=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    credential_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    daily_attempt_limit: Mapped[int] = mapped_column(Integer)
    daily_workspace_limit: Mapped[int] = mapped_column(Integer)


class DemoDay(Base):
    __tablename__ = "demo_days"
    __table_args__ = (
        CheckConstraint("workspaces_created >= 0 AND attempts_used >= 0", name="usage_positive"),
    )

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    workspaces_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    workspace_limit: Mapped[int] = mapped_column(Integer)
    attempt_limit: Mapped[int] = mapped_column(Integer)
