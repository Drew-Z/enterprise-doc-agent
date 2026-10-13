from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_doc_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AnalysisProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_project_scope"),
        CheckConstraint("draft_revision > 0", name="revision_positive"),
        CheckConstraint("jsonb_typeof(draft_spec) = 'object'", name="spec_object"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(160))
    draft_revision: Mapped[int] = mapped_column(Integer, default=1)
    draft_spec: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AnalysisSourceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_source_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analysis_snapshot_scope"),
        UniqueConstraint("project_id", "project_revision", name="uq_analysis_snapshot_revision"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["analysis_projects.tenant_id", "analysis_projects.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("project_revision > 0", name="revision_positive"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    project_id: Mapped[UUID] = mapped_column()
    project_revision: Mapped[int] = mapped_column(Integer)
    source_manifest_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AnalysisSnapshotSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analysis_snapshot_sources"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "document_version_id", name="uq_analysis_source_version"),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["analysis_source_snapshots.tenant_id", "analysis_source_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "generation_id"],
            [
                "document_ingestion_generations.tenant_id",
                "document_ingestion_generations.document_version_id",
                "document_ingestion_generations.id",
            ],
            ondelete="RESTRICT",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column()
    generation_id: Mapped[UUID] = mapped_column()
