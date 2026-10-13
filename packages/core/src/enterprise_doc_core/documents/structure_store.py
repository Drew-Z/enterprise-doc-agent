from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.documents.ingestion import DocumentParseViolation
from enterprise_doc_core.documents.structure import ParsedDocumentStructure
from enterprise_doc_core.documents.structure_models import DocumentStructure


def structure_digest(parsed: ParsedDocumentStructure) -> str:
    return hashlib.sha256(parsed.model_dump_json().encode()).hexdigest()


async def persist_structure(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_version_id: UUID,
    generation_id: UUID,
    parsed: ParsedDocumentStructure,
) -> None:
    """Caller owns the generation lease/transaction; no commit or mutable overwrite."""
    digest = structure_digest(parsed)
    existing = await session.get(DocumentStructure, generation_id)
    if existing is not None:
        if (existing.tenant_id, existing.document_version_id, existing.payload_sha256) != (
            tenant_id,
            document_version_id,
            digest,
        ):
            raise DocumentParseViolation(
                "structure_identity_changed", "A persisted structural snapshot is immutable."
            )
        return
    session.add(
        DocumentStructure(
            generation_id=generation_id,
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            source_sha256=parsed.source_sha256,
            payload_sha256=digest,
            coverage=parsed.coverage,
            payload=parsed.model_dump(mode="json"),
        )
    )


async def load_structure(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_version_id: UUID,
    generation_id: UUID,
    source_sha256: str,
) -> ParsedDocumentStructure:
    snapshot = await session.scalar(
        select(DocumentStructure).where(
            DocumentStructure.tenant_id == tenant_id,
            DocumentStructure.document_version_id == document_version_id,
            DocumentStructure.generation_id == generation_id,
        )
    )
    if snapshot is None:
        raise DocumentParseViolation("structure_not_available", "Source structure is unavailable.")
    try:
        parsed = ParsedDocumentStructure.model_validate(snapshot.payload)
        if (
            parsed.source_sha256 != source_sha256
            or snapshot.source_sha256 != source_sha256
            or snapshot.payload_sha256 != structure_digest(parsed)
            or snapshot.coverage != parsed.coverage
        ):
            raise ValueError("structure identity")
    except ValueError as error:
        raise DocumentParseViolation(
            "structure_checkpoint_invalid", "Source structure is invalid."
        ) from error
    return parsed
