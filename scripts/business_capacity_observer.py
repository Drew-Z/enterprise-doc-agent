"""Local-only observations using the existing Core retrieval and ledger contracts."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.models import UsageEvent, UsageReservation
from enterprise_doc_core.billing.product_contracts import document_processing_operation_id
from enterprise_doc_core.billing.product_models import ProductUsageEvent, ProductUsageReservation
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.object_store.multipart import MultipartObjectStore
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall
from scripts.business_capacity import BusinessFailure, LoadedCase


class CoreBusinessObserver:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: MultipartObjectStore,
        bucket: str,
        retriever: HybridRetrievalService,
        principal: PrincipalContext,
    ) -> None:
        self.sessions, self.store, self.bucket = sessions, store, bucket
        self.retriever, self.principal = retriever, principal
        self.tenant_id, self.actor_id = UUID(principal.tenant_id), UUID(principal.actor_id)

    async def ingestion(self, version_id: UUID, case: LoadedCase) -> dict[str, Any] | None:
        async with self.sessions() as session:
            version = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.tenant_id == self.tenant_id, DocumentVersion.id == version_id
                )
            )
            generation = await session.scalar(
                select(DocumentIngestionGeneration).where(
                    DocumentIngestionGeneration.tenant_id == self.tenant_id,
                    DocumentIngestionGeneration.document_version_id == version_id,
                    DocumentIngestionGeneration.active.is_(True),
                )
            )
            if version is None or generation is None:
                raise BusinessFailure("ingestion_observation_missing")
            job = await session.scalar(
                select(Job).where(
                    Job.tenant_id == self.tenant_id, Job.id == generation.processing_job_id
                )
            )
            if job is None:
                raise BusinessFailure("ingestion_job_missing")
            if job.status != "succeeded":
                return None
            chunks = (
                await session.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.tenant_id == self.tenant_id,
                        DocumentChunk.document_version_id == version_id,
                        DocumentChunk.generation_id == generation.id,
                    )
                )
            ).all()
            if (
                version.status != "ready"
                or generation.status != "succeeded"
                or version.declared_sha256 != case.spec.sha256
                or version.content_sha256_verified_at is None
                or version.size_bytes != case.spec.size_bytes
                or not chunks
                or len(chunks) != generation.chunk_count
                or generation.embedded_count != len(chunks)
                or not any(case.spec.excerpt in c.normalized_text for c in chunks)
            ):
                raise BusinessFailure("ingestion_integrity_mismatch")
            operation_id = document_processing_operation_id(job.id, job.max_attempts)
            reservation = await session.scalar(
                select(ProductUsageReservation).where(
                    ProductUsageReservation.tenant_id == self.tenant_id,
                    ProductUsageReservation.operation_id == operation_id,
                    ProductUsageReservation.metric == "document_bytes",
                )
            )
            if reservation is None:
                raise BusinessFailure("document_quota_not_observed")
            events = (
                await session.scalars(
                    select(ProductUsageEvent).where(
                        ProductUsageEvent.tenant_id == self.tenant_id,
                        ProductUsageEvent.reservation_id == reservation.id,
                    )
                )
            ).all()
            if (
                reservation.state != "consumed"
                or reservation.quantity != case.spec.size_bytes
                or len(events) != 1
                or events[0].event_type != "consume"
            ):
                raise BusinessFailure("document_accounting_mismatch")
            key, size, generation_id = version.object_key, version.size_bytes, str(generation.id)
        content = await self.store.get_range(
            bucket=self.bucket, key=key, start=0, end_inclusive=size - 1
        )
        if len(content) != size or hashlib.sha256(content).hexdigest() != case.spec.sha256:
            raise BusinessFailure("object_readback_mismatch")
        return {
            "generation_id": generation_id,
            "chunk_count": len(chunks),
            "size_bytes": size,
            "object_sha256": case.spec.sha256,
            "document_quantity": reservation.quantity,
            "document_consumptions": len(events),
        }

    async def retrieve(self, version_id: UUID, case: LoadedCase) -> dict[str, Any]:
        decision = await self.retriever.retrieve(
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            document_version_id=version_id,
            query=case.spec.query,
        )
        if not decision.accepted or not decision.candidates:
            raise BusinessFailure("retrieval_refused")
        if any(
            c.tenant_id != self.tenant_id or c.document_version_id != version_id
            for c in decision.candidates
        ):
            raise BusinessFailure("retrieval_identity_mismatch")
        if not any(case.spec.excerpt in c.text for c in decision.candidates):
            raise BusinessFailure("retrieval_anchor_missing")
        return {
            "scope": "core-service-in-observer-process",
            "candidate_count": len(decision.candidates),
            "candidate_ids": [str(c.chunk_id) for c in decision.candidates],
            "generation_ids": sorted({str(c.generation_id) for c in decision.candidates}),
            "anchor_found": True,
            "supplier_wait_ms": None,
        }

    async def ledger(self, row_id: UUID, attempt_id: UUID) -> dict[str, Any]:
        async with self.sessions() as session:
            attempts = (
                await session.scalars(
                    select(PresalesAttempt).where(
                        PresalesAttempt.tenant_id == self.tenant_id,
                        PresalesAttempt.row_id == row_id,
                    )
                )
            ).all()
            if len(attempts) != 1 or attempts[0].id != attempt_id:
                raise BusinessFailure("generation_attempt_mismatch")
            reservation = await session.scalar(
                select(UsageReservation).where(
                    UsageReservation.tenant_id == self.tenant_id,
                    UsageReservation.operation_id == attempt_id,
                )
            )
            if reservation is None:
                raise BusinessFailure("generation_quota_not_observed")
            events = (
                await session.scalars(
                    select(UsageEvent).where(
                        UsageEvent.tenant_id == self.tenant_id,
                        UsageEvent.operation_id == attempt_id,
                    )
                )
            ).all()
            calls = (
                await session.scalars(
                    select(PresalesProviderCall).where(
                        PresalesProviderCall.tenant_id == self.tenant_id,
                        PresalesProviderCall.operation_id == attempt_id,
                    )
                )
            ).all()
        return {
            "attempts": len(attempts),
            "reservation_state": reservation.state,
            "reserved_quantity": reservation.quantity,
            "consume_events": sum(e.event_type == "consume" for e in events),
            "release_events": sum(e.event_type == "release" for e in events),
            "consumed_quantity": sum(e.quantity for e in events if e.event_type == "consume"),
            "released_quantity": sum(e.quantity for e in events if e.event_type == "release"),
            "provider_calls": len(calls),
            "call_states": {
                state: sum(c.state == state for c in calls)
                for state in ("running", "succeeded", "failed", "unknown", "not_sent")
            },
            "known_total_tokens": sum(
                c.usage.get("total_tokens", 0) or 0 for c in calls if c.usage
            ),
            "calls_without_token_usage": sum(
                not c.usage or c.usage.get("total_tokens") is None for c in calls
            ),
            "currency_cost": None,
        }
