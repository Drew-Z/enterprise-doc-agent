from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.demo.limits import check_packet
from enterprise_doc_core.demo.settings import DemoError
from enterprise_doc_core.presales.access import (
    authorize_principal,
    check_key,
    check_sources,
    fingerprint,
    load_packet,
    load_row,
    source_snapshots,
)
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.export import export_csv
from enterprise_doc_core.presales.gateway import PresalesGateway
from enterprise_doc_core.presales.generation import ACTIVE_STATES, GenerationService, Retriever
from enterprise_doc_core.presales.models import (
    PresalesAttempt,
    PresalesPacket,
    PresalesReview,
    PresalesRow,
)
from enterprise_doc_core.presales.schemas import (
    AttemptView,
    BatchGenerateInput,
    BatchGenerateResult,
    CitationInput,
    CreatePacket,
    ModelDraft,
    PacketSummary,
    PacketView,
    RequirementInput,
    ReviewInput,
    RowRejection,
    RowView,
    SavedDraft,
    SavedReview,
    SourceSnapshot,
)
from enterprise_doc_core.presales.settings import PresalesSettings


class PresalesService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        retriever: Retriever,
        gateway: PresalesGateway,
        settings: PresalesSettings | None = None,
        clock: Callable[[], datetime] | None = None,
        usage_service: EntitlementUsageService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.generation = GenerationService(
            session_factory,
            retriever,
            gateway,
            settings or PresalesSettings(),
            self.clock,
            usage_service,
        )

    async def create(
        self, principal: PrincipalContext, payload: CreatePacket, key: str
    ) -> PacketView:
        check_key(key)
        digest = fingerprint(payload)
        context = get_request_context()
        async with self.session_factory.begin() as session:
            tenant_id, actor_id = await authorize_principal(session, principal, lock=True)
            packet = await session.scalar(
                select(PresalesPacket).where(
                    PresalesPacket.tenant_id == tenant_id,
                    PresalesPacket.actor_id == actor_id,
                    PresalesPacket.idempotency_key == key,
                )
            )
            if packet is not None:
                if packet.fingerprint != digest:
                    raise PresalesError("presales_idempotency_conflict")
                packet_id = packet.id
            else:
                await check_packet(session, tenant_id, len(payload.requirements), self.clock())
                sources = await source_snapshots(session, tenant_id, actor_id, payload.sources)
                packet_id = uuid4()
                session.add(
                    PresalesPacket(
                        id=packet_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        title=payload.title,
                        idempotency_key=key,
                        fingerprint=digest,
                        sources=[s.model_dump(mode="json") for s in sources],
                    )
                )
                await session.flush()
                session.add_all(
                    [
                        PresalesRow(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            packet_id=packet_id,
                            position=i,
                            requirement_key=r.key,
                            requirement_text=r.text,
                            source_location=r.source_location,
                        )
                        for i, r in enumerate(payload.requirements)
                    ]
                )
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="presales.packet.created",
                    resource_type="presales_packet",
                    resource_id=packet_id,
                    request_id=context.request_id if context else None,
                    correlation_id=context.correlation_id if context else None,
                    metadata={
                        "source_count": len(sources),
                        "requirement_count": len(payload.requirements),
                    },
                )
        return await self.get(principal, packet_id)

    async def list_packets(self, principal: PrincipalContext) -> list[PacketSummary]:
        async with self.session_factory() as session:
            tenant_id, actor_id = await authorize_principal(session, principal)
            packets = (
                await session.scalars(
                    select(PresalesPacket)
                    .where(
                        PresalesPacket.tenant_id == tenant_id, PresalesPacket.actor_id == actor_id
                    )
                    .order_by(PresalesPacket.created_at.desc(), PresalesPacket.id)
                    .limit(50)
                )
            ).all()
            result = []
            for packet in packets:
                stale = False
                try:
                    await check_sources(session, packet)
                except PresalesError as error:
                    if error.code == "presales_stale_sources":
                        stale = True
                    else:
                        continue
                count = await session.scalar(
                    select(func.count())
                    .select_from(PresalesRow)
                    .where(PresalesRow.packet_id == packet.id, PresalesRow.tenant_id == tenant_id)
                )
                result.append(
                    PacketSummary(
                        id=packet.id,
                        title=packet.title,
                        created_at=packet.created_at,
                        row_count=count or 0,
                        stale_sources=stale,
                    )
                )
            return result

    async def get(self, principal: PrincipalContext, packet_id: UUID) -> PacketView:
        async with self.session_factory() as session:
            packet = await load_packet(session, principal, packet_id)
            rows = (
                await session.scalars(
                    select(PresalesRow)
                    .where(
                        PresalesRow.packet_id == packet_id,
                        PresalesRow.tenant_id == packet.tenant_id,
                    )
                    .order_by(PresalesRow.position)
                )
            ).all()
            views = [await self._row_view(session, row) for row in rows]
            # No source text is returned after a revocation observed during assembly.
            await authorize_principal(session, principal)
            await check_sources(session, packet)
            return PacketView(
                id=packet.id,
                title=packet.title,
                created_at=packet.created_at,
                row_count=len(rows),
                sources=[SourceSnapshot.model_validate(s) for s in packet.sources],
                rows=views,
                generation_mode=(
                    "background"
                    if self.generation.settings.background_generation_enabled
                    else "synchronous"
                ),
            )

    async def generate(
        self, principal: PrincipalContext, packet_id: UUID, row_id: UUID, key: str
    ) -> PacketView:
        if self.generation.settings.background_generation_enabled:
            await self.generation.enqueue(principal, packet_id, row_id, key)
        else:
            await self.generation.generate(principal, packet_id, row_id, key)
        return await self.get(principal, packet_id)

    async def generate_batch(
        self, principal: PrincipalContext, packet_id: UUID, payload: BatchGenerateInput, key: str
    ) -> BatchGenerateResult:
        check_key(key)
        await self.get(principal, packet_id)
        if not self.generation.settings.background_generation_enabled:
            raise PresalesError("presales_background_required")
        rejected = []
        for row_id in payload.row_ids:
            row_key = hashlib.sha256(f"{key}:{row_id}".encode()).hexdigest()
            try:
                await self.generation.enqueue(principal, packet_id, row_id, row_key)
            except (PresalesError, DemoError) as error:
                if error.code in {
                    "presales_forbidden",
                    "presales_source_unavailable",
                    "presales_stale_sources",
                    "demo_session_expired",
                }:
                    raise
                rejected.append(RowRejection(row_id=row_id, code=error.code))
        return BatchGenerateResult(packet=await self.get(principal, packet_id), rejected=rejected)

    async def review(
        self,
        principal: PrincipalContext,
        packet_id: UUID,
        row_id: UUID,
        payload: ReviewInput,
        key: str,
    ) -> PacketView:
        check_key(key)
        # Preserve fingerprints of review requests saved before structured prerequisites.
        digest = fingerprint(
            payload, exclude={"prerequisites"} if payload.prerequisites is None else None
        )
        context = get_request_context()
        async with self.session_factory.begin() as session:
            packet = await load_packet(session, principal, packet_id, lock=True)
            row = await load_row(session, packet, row_id)
            existing = await session.scalar(
                select(PresalesReview).where(
                    PresalesReview.row_id == row_id,
                    PresalesReview.tenant_id == packet.tenant_id,
                    PresalesReview.idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.fingerprint != digest:
                    raise PresalesError("presales_idempotency_conflict")
            else:
                if row.draft is None:
                    raise PresalesError("presales_draft_required")
                if row.revision != payload.expected_revision:
                    raise PresalesError("presales_revision_conflict")
                if row.revision >= 101:
                    raise PresalesError("presales_review_limit")
                draft = SavedDraft.model_validate(row.draft)
                original, proposed = draft.prerequisites, payload.prerequisites
                if (original is None) != (proposed is None) or [
                    (p.condition, p.citation_indexes) for p in original or []
                ] != [(p.condition, p.citation_indexes) for p in proposed or []]:
                    raise PresalesError("presales_review_prerequisites_invalid")
                if proposed is not None:
                    previous = await session.scalar(
                        select(PresalesReview)
                        .where(
                            PresalesReview.row_id == row_id,
                            PresalesReview.tenant_id == packet.tenant_id,
                        )
                        .order_by(PresalesReview.revision.desc())
                        .limit(1)
                    )
                    previous_items = (
                        SavedReview.model_validate(previous.content).prerequisites
                        if previous is not None
                        else original
                    )
                    if not payload.note.strip() and (
                        proposed != original or proposed != previous_items
                    ):
                        raise PresalesError("presales_review_note_required")
                try:
                    ModelDraft(
                        **payload.model_dump(exclude={"expected_revision", "note"}),
                        citations=[
                            CitationInput(
                                **c.model_dump(
                                    include={"chunk_id", "document_version_id", "excerpt"}
                                )
                            )
                            for c in draft.citations
                        ],
                    )
                except ValidationError as error:
                    raise PresalesError("presales_review_evidence_required") from error
                row.revision += 1
                saved = SavedReview(
                    **payload.model_dump(exclude={"expected_revision"}),
                    revision=row.revision,
                    actor_id=packet.actor_id,
                    reviewed_at=self.clock(),
                )
                session.add(
                    PresalesReview(
                        tenant_id=packet.tenant_id,
                        row_id=row_id,
                        actor_id=packet.actor_id,
                        revision=row.revision,
                        idempotency_key=key,
                        fingerprint=digest,
                        content=saved.model_dump(mode="json"),
                    )
                )
                await append_audit_event(
                    session,
                    tenant_id=packet.tenant_id,
                    actor_id=packet.actor_id,
                    action="presales.row.reviewed",
                    resource_type="presales_packet",
                    resource_id=packet_id,
                    request_id=context.request_id if context else None,
                    correlation_id=context.correlation_id if context else None,
                    metadata={
                        "row_id": str(row_id),
                        "revision": row.revision,
                        "status": saved.status,
                    },
                )
        return await self.get(principal, packet_id)

    async def export(
        self, principal: PrincipalContext, packet_id: UUID, mode: Literal["draft", "reviewed"]
    ) -> bytes:
        context = get_request_context()
        packet = await self.get(principal, packet_id)
        content = export_csv(packet, mode)
        async with self.session_factory.begin() as session:
            current = await load_packet(session, principal, packet_id)
            await append_audit_event(
                session,
                tenant_id=current.tenant_id,
                actor_id=current.actor_id,
                action="presales.packet.exported",
                resource_type="presales_packet",
                resource_id=packet_id,
                request_id=context.request_id if context else None,
                correlation_id=context.correlation_id if context else None,
                metadata={"mode": mode, "row_count": len(packet.rows)},
            )
        return content

    async def _row_view(self, session: AsyncSession, row: PresalesRow) -> RowView:
        attempts = (
            await session.scalars(
                select(PresalesAttempt)
                .where(PresalesAttempt.row_id == row.id, PresalesAttempt.tenant_id == row.tenant_id)
                .order_by(PresalesAttempt.number)
            )
        ).all()
        reviews = (
            await session.scalars(
                select(PresalesReview)
                .where(PresalesReview.row_id == row.id, PresalesReview.tenant_id == row.tenant_id)
                .order_by(PresalesReview.revision)
            )
        ).all()
        attempt_views = []
        for attempt in attempts:
            state = (
                "expired"
                if attempt.job_id is None
                and attempt.state == "running"
                and attempt.deadline_at <= self.clock()
                else attempt.state
            )
            attempt_views.append(
                AttemptView.model_validate(
                    {
                        "id": attempt.id,
                        "number": attempt.number,
                        "state": state,
                        "error_code": "presales_attempt_expired"
                        if state == "expired"
                        else attempt.error_code,
                        "model_provider": attempt.model_provider,
                        "model_name": attempt.model_name,
                        "provider_request_count": attempt.provider_request_count,
                        "provenance": attempt.provenance,
                        "usage": attempt.usage,
                        "created_at": attempt.created_at,
                        "finished_at": attempt.finished_at,
                        "deadline_at": attempt.deadline_at,
                    }
                )
            )
        history = [SavedReview.model_validate(r.content) for r in reviews]
        state_value = "pending"
        if row.draft is not None:
            state_value = "drafted"
        elif attempt_views:
            state_value = (
                attempt_views[-1].state if attempt_views[-1].state in ACTIVE_STATES else "failed"
            )
        return RowView.model_validate(
            dict(
                id=row.id,
                requirement=RequirementInput(
                    key=row.requirement_key,
                    text=row.requirement_text,
                    source_location=row.source_location,
                ),
                revision=row.revision,
                state=state_value,
                draft=SavedDraft.model_validate(row.draft) if row.draft else None,
                review=history[-1] if history else None,
                review_history=history,
                attempts=attempt_views,
            )
        )
