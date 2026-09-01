from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin


class LocalTokenRevocation(UUIDPrimaryKeyMixin, Base):
    """Immutable tenant-scoped revocation record for a local JWT jti."""

    __tablename__ = "local_token_revocations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "token_id",
            name="uq_local_token_revocations_tenant_token",
        ),
        Index(
            "ix_local_token_revocations_expires_at",
            "expires_at",
        ),
        Index(
            "ix_local_token_revocations_tenant_actor_revoked_at",
            "tenant_id",
            "actor_id",
            "revoked_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    reason: Mapped[str] = mapped_column(String(80), nullable=False, server_default=text("'logout'"))
