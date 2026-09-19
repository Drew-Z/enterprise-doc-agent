from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, UUIDPrimaryKeyMixin

_RECEIPT_COLUMNS = (
    "accepted_at",
    "accepted_tenant_id",
    "accepted_user_id",
    "accepted_membership_id",
    "accepted_binding_id",
    "accepted_entitlement_id",
    "accepted_identity_digest",
    "accepted_request_digest",
)
_EMPTY_RECEIPT = " AND ".join(f"{name} IS NULL" for name in _RECEIPT_COLUMNS)
_FULL_RECEIPT = " AND ".join(f"{name} IS NOT NULL" for name in _RECEIPT_COLUMNS)


class TenantAdmissionGrant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tenant_admission_grants"
    __table_args__ = (
        CheckConstraint("quota_bytes > 0 AND seat_limit > 0", name="positive_entitlements"),
        CheckConstraint("token_digest ~ '^[0-9a-f]{64}$'", name="token_digest_valid"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint(
            f"(state = 'pending' AND revoked_at IS NULL AND {_EMPTY_RECEIPT}) OR "
            f"(state = 'revoked' AND revoked_at IS NOT NULL AND {_EMPTY_RECEIPT}) OR "
            f"(state = 'accepted' AND revoked_at IS NULL AND {_FULL_RECEIPT})",
            name="state_receipt_consistent",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= issued_at AND accepted_at < expires_at)",
            name="acceptance_within_validity",
        ),
    )

    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    recipient_email: Mapped[str] = mapped_column(String(320))
    issuer: Mapped[str] = mapped_column(String(512))
    quota_bytes: Mapped[int] = mapped_column(BigInteger)
    seat_limit: Mapped[int] = mapped_column(Integer)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Historical receipt IDs deliberately survive business entity deletion.
    accepted_tenant_id: Mapped[UUID | None] = mapped_column(unique=True)
    accepted_user_id: Mapped[UUID | None]
    accepted_membership_id: Mapped[UUID | None]
    accepted_binding_id: Mapped[UUID | None]
    accepted_entitlement_id: Mapped[UUID | None]
    accepted_identity_digest: Mapped[str | None] = mapped_column(String(64))
    accepted_request_digest: Mapped[str | None] = mapped_column(String(64))


class TenantInitialEntitlement(UUIDPrimaryKeyMixin, Base):
    """Initial storage configuration and enforced active-membership seat limit."""

    __tablename__ = "tenant_initial_entitlements"
    __table_args__ = (
        CheckConstraint("quota_bytes > 0 AND seat_limit > 0", name="positive_entitlements"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), unique=True
    )
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenant_admission_grants.id", ondelete="RESTRICT"), unique=True
    )
    quota_bytes: Mapped[int] = mapped_column(BigInteger)
    seat_limit: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantAdmissionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tenant_admission_events"
    __table_args__ = (
        CheckConstraint(
            "(action = 'accepted' AND accepted_user_id IS NOT NULL "
            "AND operator_id IS NULL AND reason IS NULL) OR "
            "(action IN ('issued', 'revoked') AND accepted_user_id IS NULL "
            "AND operator_id IS NOT NULL AND reason IS NOT NULL)",
            name="actor_matches_action",
        ),
        Index("ix_tenant_admission_events_grant_id_occurred_at", "grant_id", "occurred_at"),
        Index("uq_tenant_admission_events_grant_id_action", "grant_id", "action", unique=True),
    )

    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenant_admission_grants.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operator_id: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(500))
    accepted_user_id: Mapped[UUID | None]
