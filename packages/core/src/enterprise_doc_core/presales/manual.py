from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.documents.models import DocumentChunk
from enterprise_doc_core.presales.access import (
    authorize_principal,
    check_sources,
    fingerprint,
    load_packet,
    load_row,
)
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.generation import ACTIVE_STATES
from enterprise_doc_core.presales.models import PresalesAttempt
from enterprise_doc_core.presales.schemas import (
    Evidence,
    ManualAuthorshipRecord,
    ManualEvidencePage,
    ManualResponseInput,
    SavedDraft,
)


async def browse_evidence(
    session: AsyncSession,
    principal: PrincipalContext,
    packet_id: UUID,
    version_id: UUID,
    query: str,
    offset: int,
) -> ManualEvidencePage:
    if len(query) > 200 or offset < 0 or offset > 100000:
        raise PresalesError("presales_manual_evidence_invalid")
    packet = await load_packet(session, principal, packet_id)
    sources = await check_sources(session, packet)
    source = next((s for s in sources if s.version_id == version_id), None)
    if source is None:
        raise PresalesError("presales_source_unavailable")
    query = query.strip()
    start = func.greatest(func.strpos(DocumentChunk.normalized_text, query), 1) if query else 1
    statement = select(
        DocumentChunk.id,
        func.substr(DocumentChunk.normalized_text, start, 600),
        DocumentChunk.page_number,
        DocumentChunk.heading,
        DocumentChunk.start_offset,
        DocumentChunk.end_offset,
    ).where(
        DocumentChunk.tenant_id == packet.tenant_id,
        DocumentChunk.document_version_id == source.version_id,
        DocumentChunk.generation_id == source.generation_id,
    )
    if query:
        statement = statement.where(DocumentChunk.normalized_text.contains(query, autoescape=True))
    rows = (
        await session.execute(
            statement.order_by(DocumentChunk.chunk_index).offset(offset).limit(11)
        )
    ).all()
    result = ManualEvidencePage(
        items=[
            Evidence(
                chunk_id=chunk_id,
                document_version_id=source.version_id,
                excerpt=excerpt,
                filename=source.filename,
                page_number=page,
                heading=heading,
                start_offset=start_offset,
                end_offset=end_offset,
            )
            for chunk_id, excerpt, page, heading, start_offset, end_offset in rows[:10]
            if excerpt.strip()
        ],
        next_offset=offset + 10 if len(rows) > 10 and offset + 10 <= 100000 else None,
    )
    await authorize_principal(session, principal)
    await check_sources(session, packet)
    return result


async def save_response(
    session: AsyncSession,
    principal: PrincipalContext,
    packet_id: UUID,
    row_id: UUID,
    payload: ManualResponseInput,
    key: str,
    now: datetime,
) -> None:
    packet = await load_packet(session, principal, packet_id, lock=True)
    row = await load_row(session, packet, row_id)
    digest = fingerprint(payload)
    if row.manual_authorship is not None:
        previous = ManualAuthorshipRecord.model_validate(row.manual_authorship)
        if previous.idempotency_key == key:
            if previous.fingerprint != digest:
                raise PresalesError("presales_idempotency_conflict")
            return
    if row.revision != payload.expected_revision:
        raise PresalesError("presales_revision_conflict")
    if row.draft is not None:
        raise PresalesError("presales_manual_draft_exists")
    # Never cancel/expire a worker or change provider accounting as a side effect.
    if (
        await session.scalar(
            select(PresalesAttempt.id)
            .where(
                PresalesAttempt.tenant_id == packet.tenant_id,
                PresalesAttempt.row_id == row_id,
                PresalesAttempt.state.in_((*ACTIVE_STATES, "succeeded")),
            )
            .limit(1)
        )
        is not None
    ):
        raise PresalesError("presales_generation_busy")
    sources = {s.version_id: s for s in await check_sources(session, packet)}
    evidence = []
    for citation in payload.citations:
        source = sources.get(citation.document_version_id)
        if source is None:
            raise PresalesError("presales_manual_evidence_invalid")
        chunk = (
            await session.execute(
                select(
                    DocumentChunk.page_number,
                    DocumentChunk.heading,
                    DocumentChunk.start_offset,
                    DocumentChunk.end_offset,
                ).where(
                    DocumentChunk.id == citation.chunk_id,
                    DocumentChunk.tenant_id == packet.tenant_id,
                    DocumentChunk.document_version_id == source.version_id,
                    DocumentChunk.generation_id == source.generation_id,
                    DocumentChunk.normalized_text.contains(citation.excerpt, autoescape=True),
                )
            )
        ).one_or_none()
        if chunk is None:
            raise PresalesError("presales_manual_evidence_invalid")
        evidence.append(
            Evidence(
                **citation.model_dump(),
                filename=source.filename,
                page_number=chunk.page_number,
                heading=chunk.heading,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
        )
    saved = SavedDraft(
        **payload.model_dump(exclude={"expected_revision", "note", "citations"}),
        citations=evidence,
        retrieval=[],
    )
    row.draft, row.revision = saved.model_dump(mode="json"), 1
    row.manual_authorship = ManualAuthorshipRecord(
        actor_id=packet.actor_id,
        created_at=now,
        note=payload.note,
        idempotency_key=key,
        fingerprint=digest,
    ).model_dump(mode="json")
    context = get_request_context()
    await append_audit_event(
        session,
        tenant_id=packet.tenant_id,
        actor_id=packet.actor_id,
        action="presales.row.manually_authored",
        resource_type="presales_packet",
        resource_id=packet_id,
        request_id=context.request_id if context else None,
        correlation_id=context.correlation_id if context else None,
        metadata={
            "row_id": str(row_id),
            "revision": 1,
            "status": saved.status,
            "citation_count": len(evidence),
        },
    )
    await authorize_principal(session, principal)
    await check_sources(session, packet)
