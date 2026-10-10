"""Frozen rc.40 (aeab213ed5b305a9c6388de2a27b4eb32bba884e) read compatibility fixture.

Do not evolve this historical contract with current product code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import read_only_session
from enterprise_doc_core.presales.access import authorize_principal, check_sources, load_packet
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesReview, PresalesRow
from tests.presales.legacy_rc40_schema import (
    AttemptView,
    PacketView,
    RequirementInput,
    RowView,
    SavedDraft,
    SavedReview,
    SourceSnapshot,
)

ACTIVE_STATES = ("queued", "running", "recovering")


class LegacyRc40Reader:
    def __init__(self, sessions, *, background=True):
        self.session_factory = sessions
        self.generation = SimpleNamespace(
            settings=SimpleNamespace(background_generation_enabled=background)
        )
        self.clock = lambda: datetime.now(UTC)

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
                        aggregate_order_by(PresalesReview.content, PresalesReview.revision),
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
                generation_mode=(
                    "background"
                    if self.generation.settings.background_generation_enabled
                    else "synchronous"
                ),
            )

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
