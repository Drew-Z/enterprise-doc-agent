from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base


class DocumentStructure(Base):
    """One immutable, typed structural snapshot for an ingestion generation."""

    __tablename__ = "document_structures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "generation_id"],
            [
                "document_ingestion_generations.tenant_id",
                "document_ingestion_generations.document_version_id",
                "document_ingestion_generations.id",
            ],
            name="fk_document_structures_generation_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("coverage IN ('complete','partial','unknown')", name="coverage_valid"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )

    generation_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    document_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
