from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.context import PrincipalContext, get_request_context
from enterprise_doc_core.demo.limits import active_workspace, finish_attempt, reserve_attempt
from enterprise_doc_core.documents.retrieval import (
    Citation,
    CitationValidationError,
    RetrievalCandidate,
    RetrievalDecision,
    validate_citations,
)
from enterprise_doc_core.presales.access import check_key, check_sources, load_packet, load_row
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import PresalesGateway
from enterprise_doc_core.presales.models import PresalesAttempt
from enterprise_doc_core.presales.schemas import (
    Evidence,
    GenerationInput,
    ModelDraft,
    RequirementInput,
    RetrievalNote,
    SavedDraft,
    SourceSnapshot,
)
from enterprise_doc_core.presales.settings import PresalesSettings

PIPELINE_VERSION = "presales-workspace.v1"


class Retriever(Protocol):
    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None = None,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision: ...


def resolve_draft(
    draft: ModelDraft,
    candidates: tuple[RetrievalCandidate, ...],
    sources: list[SourceSnapshot],
    tenant_id: UUID,
    notes: list[RetrievalNote],
) -> SavedDraft:
    versions = {source.version_id for source in sources}
    evidence = []
    try:
        for proposed in draft.citations:
            if proposed.document_version_id not in versions:
                raise PresalesError("presales_invalid_citation", provider_requests=1)
            resolved = validate_citations(
                (Citation(proposed.chunk_id, proposed.document_version_id, proposed.excerpt),),
                candidates,
                tenant_id=tenant_id,
                document_version_id=proposed.document_version_id,
                max_excerpt_chars=600,
            )[0]
            evidence.append(
                Evidence(
                    **proposed.model_dump(),
                    filename=resolved.source_filename or "",
                    page_number=resolved.page_number,
                    heading=resolved.heading,
                    start_offset=resolved.start_offset,
                    end_offset=resolved.end_offset,
                )
            )
    except CitationValidationError as error:
        raise PresalesError("presales_invalid_citation", provider_requests=1) from error
    return SavedDraft(
        **draft.model_dump(exclude={"citations"}), citations=evidence, retrieval=notes
    )


class GenerationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        retriever: Retriever,
        gateway: PresalesGateway,
        settings: PresalesSettings,
        clock: Callable[[], datetime],
        usage_service: EntitlementUsageService | None = None,
    ) -> None:
        self.session_factory, self.retriever, self.gateway, self.settings, self.clock = (
            session_factory,
            retriever,
            gateway,
            settings,
            clock,
        )
        self.usage_service = usage_service

    async def generate(
        self, principal: PrincipalContext, packet_id: UUID, row_id: UUID, key: str
    ) -> None:
        check_key(key)
        started = await self._begin(principal, packet_id, row_id, key)
        if started is None:
            return
        attempt_id, requirement, sources = started
        tenant_id, actor_id = UUID(principal.tenant_id), UUID(principal.actor_id)
        context = get_request_context()
        provider_requests: int | None = 0
        try:
            async with asyncio.timeout(self.settings.row_timeout_seconds):
                candidates, notes = await self._retrieve(tenant_id, actor_id, requirement, sources)
                # ACL may have changed during retrieval. Recheck before sending any evidence.
                async with self.session_factory() as session:
                    await load_packet(session, principal, packet_id)
                payload = GenerationInput(
                    requirement=requirement,
                    sources=sources,
                    evidence=[
                        {
                            "chunkId": str(c.chunk_id),
                            "documentVersionId": str(c.document_version_id),
                            "text": c.text,
                            "filename": c.source_filename or "",
                            "heading": c.heading or "",
                            "pageNumber": str(c.page_number or ""),
                        }
                        for c in candidates
                    ],
                )
                # Persist uncertainty before entering the gateway. A crash cannot
                # silently turn an in-flight request into a recorded zero.
                await self._prepare_dispatch(principal, packet_id, row_id, attempt_id)
                provider_requests = None
                try:
                    generated = await self.gateway.generate(payload)
                except PresalesError as error:
                    provider_requests = error.provider_requests
                    raise
                provider_requests = 1
                saved = resolve_draft(generated.draft, candidates, sources, tenant_id, notes)
            async with self.session_factory.begin() as session:
                packet = await load_packet(session, principal, packet_id, lock=True)
                row = await load_row(session, packet, row_id)
                attempt = await session.get(PresalesAttempt, attempt_id, with_for_update=True)
                if (
                    attempt is None
                    or attempt.state != "running"
                    or attempt.deadline_at <= self.clock()
                    or row.draft is not None
                ):
                    raise PresalesError("presales_attempt_expired", provider_requests=1)
                attempt.state, attempt.finished_at = "succeeded", self.clock()
                await finish_attempt(session, tenant_id, attempt_id)
                attempt.provider_request_count, attempt.usage = 1, generated.usage
                if self.usage_service is not None:
                    try:
                        await self.usage_service.settle_provider_request(
                            tenant_id=tenant_id,
                            operation_id=attempt_id,
                            provider=self.gateway.model_provider,
                            model=generated.returned_model or self.gateway.model_name,
                            usage=generated.usage,
                            source="presales",
                            session=session,
                        )
                    except UsageError as error:
                        raise PresalesError("presales_usage_settlement_failed") from error
                attempt.provenance = {
                    **attempt.provenance,
                    "returnedModel": generated.returned_model,
                    "providerResponseId": generated.provider_response_id,
                }
                row.draft, row.revision = saved.model_dump(mode="json"), 1
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="presales.row.generated",
                    resource_type="presales_packet",
                    resource_id=packet_id,
                    request_id=context.request_id if context else None,
                    correlation_id=context.correlation_id if context else None,
                    metadata={
                        "row_id": str(row_id),
                        "attempt_id": str(attempt_id),
                        "status": saved.status,
                    },
                )
        except PresalesError as error:
            await self._fail(attempt_id, error.code, provider_requests)
        except TimeoutError:
            await self._fail(attempt_id, "presales_generation_timeout", provider_requests)
        except asyncio.CancelledError:
            await self._fail(attempt_id, "presales_generation_interrupted", provider_requests)
            raise
        except Exception:
            # Boundary failure details may include document/provider content.
            await self._fail(attempt_id, "presales_generation_failed", provider_requests)

    async def _begin(
        self, principal: PrincipalContext, packet_id: UUID, row_id: UUID, key: str
    ) -> tuple[UUID, RequirementInput, list[SourceSnapshot]] | None:
        async with self.session_factory.begin() as session:
            packet = await load_packet(session, principal, packet_id, lock=True)
            row = await load_row(session, packet, row_id)
            attempts = list(
                (
                    await session.scalars(
                        select(PresalesAttempt)
                        .where(
                            PresalesAttempt.row_id == row_id,
                            PresalesAttempt.tenant_id == packet.tenant_id,
                        )
                        .order_by(PresalesAttempt.number)
                    )
                ).all()
            )
            if any(a.idempotency_key == key for a in attempts) or row.draft is not None:
                return None
            if not self.settings.generation_enabled:
                raise PresalesError("presales_generation_disabled")
            if self.gateway.model_provider == "deterministic":
                raise PresalesError("presales_model_not_configured")
            now = self.clock()
            if attempts and attempts[-1].state == "running" and attempts[-1].deadline_at > now:
                raise PresalesError("presales_generation_busy")
            if len(attempts) >= 3:
                raise PresalesError("presales_attempt_limit")
            if attempts and attempts[-1].state == "running":
                attempts[-1].state, attempts[-1].error_code, attempts[-1].finished_at = (
                    "expired",
                    "presales_attempt_expired",
                    now,
                )
            day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            daily_count = await session.scalar(
                select(func.count())
                .select_from(PresalesAttempt)
                .where(
                    PresalesAttempt.tenant_id == packet.tenant_id,
                    PresalesAttempt.created_at >= day_start,
                )
            )
            active_count = await session.scalar(
                select(func.count())
                .select_from(PresalesAttempt)
                .where(
                    PresalesAttempt.tenant_id == packet.tenant_id,
                    PresalesAttempt.state == "running",
                    PresalesAttempt.deadline_at > now,
                )
            )
            if (daily_count or 0) >= self.settings.daily_attempt_limit:
                raise PresalesError("presales_daily_limit")
            if (active_count or 0) >= self.settings.concurrent_attempt_limit:
                raise PresalesError("presales_generation_busy")
            attempt_id = uuid4()
            await reserve_attempt(
                session,
                packet.tenant_id,
                attempt_id,
                now,
                now + timedelta(seconds=self.settings.row_timeout_seconds),
            )
            session.add(
                PresalesAttempt(
                    id=attempt_id,
                    tenant_id=packet.tenant_id,
                    row_id=row_id,
                    number=len(attempts) + 1,
                    idempotency_key=key,
                    state="running",
                    model_provider=self.gateway.model_provider,
                    model_name=self.gateway.model_name,
                    provenance={
                        **self.gateway.provenance,
                        "pipelineVersion": PIPELINE_VERSION,
                    },
                    deadline_at=now + timedelta(seconds=self.settings.row_timeout_seconds),
                    created_at=now,
                )
            )
            if self.usage_service is not None:
                try:
                    await self.usage_service.reserve_provider_request(
                        tenant_id=packet.tenant_id,
                        operation_id=attempt_id,
                        source="presales",
                        session=session,
                    )
                except UsageError as error:
                    if error.code == "usage_entitlement_inactive":
                        raise PresalesError("presales_entitlement_inactive") from error
                    if error.code == "usage_limit_reached":
                        raise PresalesError("presales_usage_limit") from error
                    raise PresalesError("presales_usage_unavailable") from error
            return (
                attempt_id,
                RequirementInput(
                    key=row.requirement_key,
                    text=row.requirement_text,
                    source_location=row.source_location,
                ),
                await check_sources(session, packet),
            )

    async def _retrieve(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        requirement: RequirementInput,
        sources: list[SourceSnapshot],
    ) -> tuple[tuple[RetrievalCandidate, ...], list[RetrievalNote]]:
        candidates: list[RetrievalCandidate] = []
        notes = []
        for source in sources:
            result = await self.retriever.retrieve(
                tenant_id=tenant_id,
                actor_id=actor_id,
                document_version_id=source.version_id,
                query=requirement.text,
            )
            available = result.candidates if result.accepted else ()
            used = available[:2]
            if any(
                c.tenant_id != tenant_id
                or c.document_version_id != source.version_id
                or c.generation_id != source.generation_id
                for c in used
            ):
                raise PresalesError("presales_invalid_evidence")
            notes.append(
                RetrievalNote(
                    version_id=source.version_id,
                    retrieved_count=len(result.candidates),
                    used_count=len(used),
                    truncated=len(available) > 2 or any(len(c.text) > 1800 for c in used),
                )
            )
            candidates.extend(
                replace(c, text=c.text[:1800], end_offset=min(c.end_offset, c.start_offset + 1800))
                for c in used
            )
        return tuple(candidates), notes

    async def _prepare_dispatch(
        self, principal: PrincipalContext, packet_id: UUID, row_id: UUID, attempt_id: UUID
    ) -> None:
        async with self.session_factory.begin() as session:
            packet = await load_packet(session, principal, packet_id, lock=True)
            await active_workspace(session, packet.tenant_id, self.clock())
            row = await load_row(session, packet, row_id)
            attempt = await session.get(PresalesAttempt, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.state != "running"
                or attempt.deadline_at <= self.clock()
                or row.draft is not None
            ):
                raise PresalesError("presales_attempt_expired")
            attempt.provider_request_count = None

    async def _fail(self, attempt_id: UUID, code: str, provider_requests: int | None) -> None:
        tenant_id: UUID | None = None
        async with self.session_factory.begin() as session:
            attempt = await session.get(PresalesAttempt, attempt_id, with_for_update=True)
            if attempt is not None:
                tenant_id = attempt.tenant_id
                await finish_attempt(session, tenant_id, attempt_id)
            if attempt is not None and attempt.state == "running":
                attempt.state = "expired" if attempt.deadline_at <= self.clock() else "failed"
                attempt.error_code, attempt.finished_at = code, self.clock()
                attempt.provider_request_count = provider_requests
            elif attempt is not None and attempt.state == "expired":
                # An old execution may report accounting, but never replace a
                # newer draft or change the expired attempt back to succeeded.
                if attempt.provider_request_count is None and provider_requests is not None:
                    attempt.provider_request_count = provider_requests
        if self.usage_service is not None and tenant_id is not None:
            try:
                await self.usage_service.release_provider_request(
                    tenant_id=tenant_id,
                    operation_id=attempt_id,
                    source="presales",
                )
            except UsageError:
                pass
