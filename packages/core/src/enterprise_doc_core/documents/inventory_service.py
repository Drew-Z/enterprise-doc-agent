from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from enterprise_doc_core.documents.models import (
    Document,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.policy import document_visible_to_actor


@dataclass(frozen=True, slots=True)
class DocumentInventoryItemResult:
    document_id: UUID
    title: str
    access_mode: str
    can_manage: bool
    version_id: UUID
    version_number: int
    filename: str
    media_type: str
    size_bytes: int
    version_status: str
    generation_id: UUID | None
    ingestion_status: str | None
    ingestion_stage: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class DocumentInventoryService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None = None,
        role: str | None = None,
        limit: int = 100,
    ) -> tuple[DocumentInventoryItemResult, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("document inventory limit must be between 1 and 200")

        latest_generation = aliased(DocumentIngestionGeneration)
        latest_generation_id = (
            select(latest_generation.id)
            .where(
                latest_generation.tenant_id == tenant_id,
                latest_generation.document_version_id == DocumentVersion.id,
            )
            .order_by(
                latest_generation.created_at.desc(),
                latest_generation.id.desc(),
            )
            .limit(1)
            .correlate(DocumentVersion)
            .scalar_subquery()
        )
        statement = (
            select(Document, DocumentVersion, DocumentIngestionGeneration)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .outerjoin(
                DocumentIngestionGeneration,
                DocumentIngestionGeneration.id == latest_generation_id,
            )
            .where(
                document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                DocumentVersion.tenant_id == tenant_id,
            )
            .order_by(DocumentVersion.updated_at.desc(), DocumentVersion.id.desc())
            .limit(limit)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()

        return tuple(
            DocumentInventoryItemResult(
                document_id=document.id,
                title=document.title,
                access_mode=document.access_mode,
                can_manage=(
                    actor_id is not None and (document.created_by == actor_id or role == "owner")
                ),
                version_id=version.id,
                version_number=version.version_number,
                filename=version.original_filename,
                media_type=version.detected_media_type,
                size_bytes=version.size_bytes,
                version_status=version.status,
                generation_id=generation.id if generation is not None else None,
                ingestion_status=generation.status if generation is not None else None,
                ingestion_stage=generation.stage if generation is not None else None,
                error_code=generation.error_code if generation is not None else None,
                created_at=version.created_at,
                updated_at=version.updated_at,
            )
            for document, version, generation in rows
        )
