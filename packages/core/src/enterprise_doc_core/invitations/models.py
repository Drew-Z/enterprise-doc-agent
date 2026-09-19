from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin

_RECEIPT = (
    "accepted_at",
    "accepted_user_id",
    "accepted_membership_id",
    "accepted_binding_id",
    "accepted_identity_digest",
)
_EMPTY = " AND ".join(f"{name} IS NULL" for name in _RECEIPT)
_FULL = " AND ".join(f"{name} IS NOT NULL" for name in _RECEIPT)


class MembershipInvitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "membership_invitations"
    __table_args__ = (
        CheckConstraint("generation > 0", name="positive_generation"),
        CheckConstraint("token_digest ~ '^[0-9a-f]{64}$'", name="token_digest_valid"),
        CheckConstraint(
            "expires_at > issued_at AND issued_at >= created_at AND updated_at >= issued_at",
            name="valid_times",
        ),
        CheckConstraint(
            f"(state = 'pending' AND revoked_at IS NULL AND {_EMPTY}) OR "
            f"(state = 'revoked' AND revoked_at IS NOT NULL AND {_EMPTY}) OR "
            f"(state = 'accepted' AND revoked_at IS NULL AND {_FULL})",
            name="state_receipt_consistent",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= issued_at AND accepted_at < expires_at)",
            name="acceptance_within_validity",
        ),
        Index(
            "uq_membership_invitations_pending_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_membership_invitations_tenant_created", "tenant_id", "created_at"),
    )

    # Historical identities must survive deletion of live business entities.
    tenant_id: Mapped[UUID]
    email: Mapped[str] = mapped_column(String(320))
    issuer: Mapped[str] = mapped_column(String(512))
    issued_by_user_id: Mapped[UUID]
    issued_by_membership_id: Mapped[UUID]
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    generation: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    state: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[UUID | None]
    accepted_membership_id: Mapped[UUID | None]
    accepted_binding_id: Mapped[UUID | None]
    accepted_identity_digest: Mapped[str | None] = mapped_column(String(64))


class MembershipInvitationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "membership_invitation_events"
    __table_args__ = (
        CheckConstraint("generation > 0", name="positive_generation"),
        CheckConstraint(
            "(action = 'accepted' AND operation_id IS NULL AND request_digest IS NULL) OR "
            "(action IN ('issued', 'regenerated', 'revoked') AND operation_id IS NOT NULL "
            "AND request_digest IS NOT NULL AND request_digest ~ '^[0-9a-f]{64}$')",
            name="valid_operation",
        ),
        Index("uq_membership_invitation_operation", "tenant_id", "operation_id", unique=True),
        Index(
            "uq_membership_invitation_generation_action",
            "invitation_id",
            "generation",
            "action",
            unique=True,
        ),
        Index("ix_membership_invitation_events_tenant_time", "tenant_id", "occurred_at"),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("membership_invitations.id", ondelete="RESTRICT")
    )
    tenant_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    generation: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    operation_id: Mapped[UUID | None]
    request_digest: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
