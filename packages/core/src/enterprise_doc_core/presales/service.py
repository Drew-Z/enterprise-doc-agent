from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.db import read_only_session
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
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway, PresalesGateway
from enterprise_doc_core.presales.generation import ACTIVE_STATES, GenerationService, Retriever
from enterprise_doc_core.presales.models import (
    PresalesAttempt,
    PresalesPacket,
    PresalesReview,
    PresalesRow,
)
from enterprise_doc_core.presales.policy import ExecutionMode
from enterprise_doc_core.presales.review import validate_review
from enterprise_doc_core.presales.schemas import (
    AttemptView,
    BatchGenerateInput,
    BatchGenerateResult,
    CreatePacket,
    GenerationReceipt,
    PacketSummary,
    PacketView,
    RequirementInput,
    ReviewInput,
    RowRejection,
    RowView,
    SavedDraft,
    SavedReview,
    SourceSnapshot,
    WorkbookImportInput,
    WorkbookPacket,
)
from enterprise_doc_core.presales.settings import PresalesSettings
from enterprise_doc_core.presales.workbook import (
    decode_workbook,
    inspect_workbook,
)
from enterprise_doc_core.presales.workbook import (
    export_workbook as render_workbook,
)
from enterprise_doc_core.presales.workbook_schemas import (
    MAX_TENANT_WORKBOOK_BYTES,
    WorkbookMetadata,
    WorkbookPreview,
    WorkbookUpload,
)


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
        return await self._create(principal, payload, key)

    async def preview_workbook(
        self, principal: PrincipalContext, payload: WorkbookUpload
    ) -> WorkbookPreview:
        async with read_only_session(self.session_factory) as session:
            await authorize_principal(session, principal)
        content = decode_workbook(payload)
        return await asyncio.to_thread(inspect_workbook, content, payload.filename, payload.mapping)

    async def import_workbook(
        self, principal: PrincipalContext, payload: WorkbookImportInput, key: str
    ) -> PacketView:
        check_key(key)
        preview = await self.preview_workbook(principal, payload)
        if preview.sha256 != payload.confirmed_sha256:
            raise PresalesError("presales_workbook_mismatch")
        packet = WorkbookPacket(
            title=payload.title,
            sources=payload.sources,
            requirements=[
                RequirementInput(
                    key=f"X{question.row}",
                    text=question.text,
                    source_location=(
                        f"{payload.mapping.sheet}!{question.question_cell} → {question.answer_cell}"
                    ),
                )
                for question in preview.questions
            ],
        )
        metadata = WorkbookMetadata(
            filename=preview.filename,
            sha256=preview.sha256,
            mapping=payload.mapping,
            rows=[question.row for question in preview.questions],
        )
        return await self._create(
            principal, packet, key, metadata=metadata, content=decode_workbook(payload)
        )

    async def _create(
        self,
        principal: PrincipalContext,
        payload: CreatePacket,
        key: str,
        *,
        metadata: WorkbookMetadata | None = None,
        content: bytes | None = None,
    ) -> PacketView:
        check_key(key)
        digest = fingerprint(payload)
        if metadata is not None:
            digest = hashlib.sha256((digest + metadata.model_dump_json()).encode()).hexdigest()
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
                if content is not None:
                    stored = await session.scalar(
                        select(
                            func.coalesce(
                                func.sum(func.octet_length(PresalesPacket.workbook_content)), 0
                            )
                        ).where(PresalesPacket.tenant_id == tenant_id)
                    )
                    if (stored or 0) + len(content) > MAX_TENANT_WORKBOOK_BYTES:
                        raise PresalesError("presales_workbook_storage_limit")
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
                        workbook_metadata=metadata.model_dump(mode="json") if metadata else None,
                        workbook_content=content,
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
        async with read_only_session(self.session_factory) as session:
            packet = await load_packet(session, principal, packet_id)
            # One statement keeps draft/revision and their histories in the same
            # MVCC snapshot when a generation or review commits during this read.
            # Aggregate each history separately to avoid multiplying joined rows.
            attempt_content = func.jsonb_build_object(
                *[
                    value
                    for name in (*AttemptView.model_fields, "job_id")
                    for value in (name, getattr(PresalesAttempt, name))
                ]
            )
            attempts = (
                select(
                    func.jsonb_agg(
                        aggregate_order_by(attempt_content, PresalesAttempt.number), type_=JSONB
                    )
                )
                .where(
                    PresalesAttempt.row_id == PresalesRow.id,
                    PresalesAttempt.tenant_id == PresalesRow.tenant_id,
                )
                .correlate(PresalesRow)
                .scalar_subquery()
            )
            reviews = (
                select(
                    func.jsonb_agg(
                        aggregate_order_by(
                            PresalesReview.content.op("||")(
                                func.jsonb_build_object(
                                    "prerequisite_changes", PresalesReview.prerequisite_changes
                                )
                            ),
                            PresalesReview.revision,
                        ),
                        type_=JSONB,
                    )
                )
                .where(
                    PresalesReview.row_id == PresalesRow.id,
                    PresalesReview.tenant_id == PresalesRow.tenant_id,
                )
                .correlate(PresalesRow)
                .scalar_subquery()
            )
            rows = (
                await session.execute(
                    select(PresalesRow, attempts, reviews)
                    .where(
                        PresalesRow.packet_id == packet_id,
                        PresalesRow.tenant_id == packet.tenant_id,
                    )
                    .order_by(PresalesRow.position)
                )
            ).all()
            views = [
                self._row_view(row, row_attempts or [], row_reviews or [])
                for row, row_attempts, row_reviews in rows
            ]
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
                workbook=WorkbookMetadata.model_validate(packet.workbook_metadata)
                if packet.workbook_metadata
                else None,
                generation_mode=(
                    "background"
                    if self.generation.settings.background_generation_enabled
                    else "synchronous"
                ),
                available_execution_modes=(
                    ["auto", "deep"]
                    if self.generation.settings.background_generation_enabled
                    else ["auto"]
                )
                if isinstance(self.generation.gateway, OpenAICompatiblePresalesGateway)
                and self.generation.gateway.model_provider == "openai_compatible"
                else [],
            )

    async def generate(
        self,
        principal: PrincipalContext,
        packet_id: UUID,
        row_id: UUID,
        key: str,
        *,
        execution_mode: ExecutionMode | None = None,
    ) -> PacketView:
        if self.generation.settings.background_generation_enabled:
            await self.generation.enqueue(
                principal, packet_id, row_id, key, execution_mode=execution_mode
            )
        else:
            await self.generation.generate(
                principal, packet_id, row_id, key, execution_mode=execution_mode
            )
        return await self.get(principal, packet_id)

    async def generate_batch(
        self, principal: PrincipalContext, packet_id: UUID, payload: BatchGenerateInput, key: str
    ) -> BatchGenerateResult:
        receipt = await self.admit_batch(principal, packet_id, payload, key)
        return BatchGenerateResult(
            packet=await self.get(principal, packet_id), rejected=receipt.rejected
        )

    async def admit(
        self,
        principal: PrincipalContext,
        packet_id: UUID,
        row_id: UUID,
        key: str,
        *,
        execution_mode: ExecutionMode | None = None,
    ) -> GenerationReceipt:
        check_key(key)
        if not self.generation.settings.background_generation_enabled:
            await self.get(principal, packet_id)
            raise PresalesError("presales_background_required")
        admitted = await self.generation.enqueue(
            principal, packet_id, row_id, key, execution_mode=execution_mode
        )
        return GenerationReceipt(packet_id=packet_id, admissions=[admitted], rejected=[])

    async def admit_batch(
        self, principal: PrincipalContext, packet_id: UUID, payload: BatchGenerateInput, key: str
    ) -> GenerationReceipt:
        check_key(key)
        if not self.generation.settings.background_generation_enabled:
            await self.get(principal, packet_id)
            raise PresalesError("presales_background_required")
        # Each enqueue reauthorizes and commits independently. A receipt contains
        # identifiers only; callers obtain content through the authorized GET.
        admissions = []
        rejected = []
        for row_id in payload.row_ids:
            row_key = hashlib.sha256(f"{key}:{row_id}".encode()).hexdigest()
            try:
                admissions.append(
                    await self.generation.enqueue(
                        principal, packet_id, row_id, row_key, execution_mode=payload.execution_mode
                    )
                )
            except (PresalesError, DemoError) as error:
                if error.code in {
                    "presales_forbidden",
                    "presales_source_unavailable",
                    "presales_stale_sources",
                    "demo_session_expired",
                }:
                    raise
                if error.code == "presales_not_found":
                    # Distinguish an inaccessible packet from a missing row without
                    # assembling content or adding reads to successful admissions.
                    async with read_only_session(self.session_factory) as session:
                        await load_packet(session, principal, packet_id)
                rejected.append(RowRejection(row_id=row_id, code=error.code))
        return GenerationReceipt(packet_id=packet_id, admissions=admissions, rejected=rejected)

    async def review(
        self,
        principal: PrincipalContext,
        packet_id: UUID,
        row_id: UUID,
        payload: ReviewInput,
        key: str,
    ) -> PacketView:
        check_key(key)
        # Preserve every earlier wire fingerprint; explicit change maps are part of new intents.
        excluded = {"prerequisite_changes"} if payload.prerequisite_changes is None else set()
        if payload.prerequisites is None:
            excluded.add("prerequisites")
        digest = fingerprint(payload, exclude=excluded)
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
                previous_review = None
                if payload.prerequisites is not None or row.revision > 1:
                    previous = await session.scalar(
                        select(PresalesReview)
                        .where(
                            PresalesReview.row_id == row_id,
                            PresalesReview.tenant_id == packet.tenant_id,
                        )
                        .order_by(PresalesReview.revision.desc())
                        .limit(1)
                    )
                    previous_review = (
                        SavedReview.model_validate(
                            {
                                **previous.content,
                                "prerequisite_changes": previous.prerequisite_changes,
                            }
                        )
                        if previous is not None
                        else None
                    )
                validate_review(draft, payload, previous_review)
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
                        content=saved.model_dump(mode="json", exclude={"prerequisite_changes"}),
                        prerequisite_changes=saved.prerequisite_changes.model_dump(mode="json")
                        if saved.prerequisite_changes is not None
                        else None,
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

    async def export_workbook(
        self, principal: PrincipalContext, packet_id: UUID, mode: Literal["draft", "reviewed"]
    ) -> bytes:
        packet = await self.get(principal, packet_id)
        if packet.workbook is None:
            raise PresalesError("presales_workbook_missing")
        async with read_only_session(self.session_factory) as session:
            current = await load_packet(session, principal, packet_id)
            original = await session.scalar(
                select(PresalesPacket.workbook_content).where(
                    PresalesPacket.id == current.id,
                    PresalesPacket.tenant_id == current.tenant_id,
                    PresalesPacket.actor_id == current.actor_id,
                )
            )
        if original is None:
            raise PresalesError("presales_workbook_missing")
        content = await asyncio.to_thread(render_workbook, original, packet.workbook, packet, mode)
        context = get_request_context()
        async with self.session_factory.begin() as session:
            current = await load_packet(session, principal, packet_id)
            await append_audit_event(
                session,
                tenant_id=current.tenant_id,
                actor_id=current.actor_id,
                action="presales.workbook.exported",
                resource_type="presales_packet",
                resource_id=packet_id,
                request_id=context.request_id if context else None,
                correlation_id=context.correlation_id if context else None,
                metadata={
                    "mode": mode,
                    "row_count": len(packet.rows),
                    "sha256": packet.workbook.sha256,
                },
            )
        return content

    def _row_view(
        self, row: PresalesRow, attempts: list[dict[str, Any]], reviews: list[dict[str, Any]]
    ) -> RowView:
        attempt_views = []
        for content in attempts:
            attempt = AttemptView.model_validate(
                {key: value for key, value in content.items() if key != "job_id"}
            )
            if (
                content["job_id"] is None
                and attempt.state == "running"
                and attempt.deadline_at <= self.clock()
            ):
                attempt = attempt.model_copy(
                    update={"state": "expired", "error_code": "presales_attempt_expired"}
                )
            attempt_views.append(attempt)
        history = [SavedReview.model_validate(content) for content in reviews]
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
