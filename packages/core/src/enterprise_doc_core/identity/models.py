from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MembershipRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("quota_bytes > 0", name="quota_bytes_positive"),
        CheckConstraint(
            "used_storage_bytes >= 0 AND reserved_storage_bytes >= 0",
            name="storage_counters_non_negative",
        ),
        CheckConstraint(
            "used_storage_bytes + reserved_storage_bytes <= quota_bytes",
            name="storage_within_quota",
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_storage_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    reserved_storage_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )


class ExternalIdentityBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit issuer/subject binding for external IdP identities."""

    __tablename__ = "external_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "issuer",
            "subject",
            name="uq_external_identity_bindings_tenant_issuer_subject",
        ),
        Index(
            "ix_external_identity_bindings_tenant_id_user_id_is_active",
            "tenant_id",
            "user_id",
            "is_active",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            name="uq_memberships_tenant_id_user_id",
        ),
        CheckConstraint("role IN ('owner', 'member')", name="role_valid"),
        Index("ix_memberships_user_id_is_active", "user_id", "is_active"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
