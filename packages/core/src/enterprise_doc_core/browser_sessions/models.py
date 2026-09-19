from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin

_NO_SELECTION = " AND ".join(
    f"{name} IS NULL" for name in ("tenant_id", "user_id", "membership_id", "binding_id")
)


class BrowserSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        CheckConstraint("credential_digest ~ '^[0-9a-f]{64}$'", name="credential_digest_valid"),
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            f"({_NO_SELECTION}) OR ({_NO_SELECTION.replace('IS NULL', 'IS NOT NULL')})",
            name="selection_complete",
        ),
        Index("ix_browser_sessions_expires_at", "expires_at"),
    )

    credential_digest: Mapped[str] = mapped_column(String(64), unique=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Snapshot references intentionally survive removal of business entities.
    tenant_id: Mapped[UUID | None]
    user_id: Mapped[UUID | None]
    membership_id: Mapped[UUID | None]
    binding_id: Mapped[UUID | None]


class BrowserLoginAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "browser_login_attempts"
    __table_args__ = (
        CheckConstraint(
            "state_digest ~ '^[0-9a-f]{64}$' AND verifier_digest ~ '^[0-9a-f]{64}$' "
            "AND nonce_digest ~ '^[0-9a-f]{64}$' AND client_digest ~ '^[0-9a-f]{64}$'",
            name="digests_valid",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at < expires_at)",
            name="consumption_within_validity",
        ),
        CheckConstraint(
            "completed_at IS NULL OR (consumed_at IS NOT NULL "
            "AND completed_at >= consumed_at AND completed_at < expires_at)",
            name="completion_after_consumption",
        ),
        CheckConstraint(
            "(previous_session_id IS NULL AND previous_generation IS NULL) OR "
            "(previous_session_id IS NOT NULL AND previous_generation > 0 "
            "AND previous_credential_digest IS NOT NULL)",
            name="previous_context_complete",
        ),
        CheckConstraint(
            "(completed_at IS NULL AND completed_session_id IS NULL "
            "AND completed_generation IS NULL) OR (completed_at IS NOT NULL "
            "AND completed_session_id IS NOT NULL AND completed_generation > 0)",
            name="completed_context_complete",
        ),
        Index("ix_browser_login_attempts_client_digest_created_at", "client_digest", "created_at"),
    )

    state_digest: Mapped[str] = mapped_column(String(64), unique=True)
    verifier_digest: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_digest: Mapped[str] = mapped_column(String(64))
    client_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_session_id: Mapped[UUID | None]
    completed_generation: Mapped[int | None] = mapped_column(BigInteger)
    previous_session_id: Mapped[UUID | None]
    previous_generation: Mapped[int | None] = mapped_column(BigInteger)
    previous_credential_digest: Mapped[str | None] = mapped_column(String(64))


class BrowserSessionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "browser_session_events"
    __table_args__ = (
        CheckConstraint("action IN ('signed_in', 'selected', 'signed_out')", name="action_valid"),
        CheckConstraint("generation > 0", name="generation_positive"),
        UniqueConstraint("session_id", "generation"),
    )

    session_id: Mapped[UUID] = mapped_column(ForeignKey("browser_sessions.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[UUID | None]
    user_id: Mapped[UUID | None]
