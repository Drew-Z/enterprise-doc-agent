"""Safe database and object evidence for the isolated first-use browser run."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.identity.models import Membership, Tenant
from enterprise_doc_core.jobs.models import Job, JobAttempt, OutboxEvent
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesPacket, PresalesReview
from enterprise_doc_core.uploads.models import UploadSession

if TYPE_CHECKING:
    from tests.first_use.harness import FirstUseHarness


async def pipeline_snapshot(harness: FirstUseHarness) -> dict[str, object]:
    await harness.owned_tenants()
    async with harness.sessions() as session:
        assert await session.scalar(text("SELECT current_schema()")) == harness.schema
        tenants = (await session.scalars(select(Tenant))).all()
        members = (await session.scalars(select(Membership))).all()
        uploads = (await session.scalars(select(UploadSession))).all()
        versions = (await session.scalars(select(DocumentVersion))).all()
        generations = (await session.scalars(select(DocumentIngestionGeneration))).all()
        chunks = (await session.scalars(select(DocumentChunk))).all()
        jobs = (await session.scalars(select(Job))).all()
        job_attempts = (await session.scalars(select(JobAttempt))).all()
        outbox = (await session.scalars(select(OutboxEvent))).all()
        packets = (await session.scalars(select(PresalesPacket))).all()
        attempts = (await session.scalars(select(PresalesAttempt))).all()
        reviews = (await session.scalars(select(PresalesReview))).all()
        entitlements = (await session.scalars(select(TenantEntitlement))).all()
        reservations = (await session.scalars(select(UsageReservation))).all()
        events = (await session.scalars(select(UsageEvent))).all()
        audits = (await session.scalars(select(AuditEvent))).all()
    object_hashes: dict[str, str] = {}
    for version in versions:
        content = await harness.resources.multipart_object_store.get_range(
            bucket=harness.settings.object_store.documents_bucket,
            key=version.object_key,
            start=0,
            end_inclusive=version.size_bytes - 1,
        )
        object_hashes[str(version.id)] = hashlib.sha256(content).hexdigest()
    return {
        "tenants": [
            {
                "id": str(item.id),
                "name": item.name,
                "storageUsedBytes": item.used_storage_bytes,
                "storageReservedBytes": item.reserved_storage_bytes,
                "storageLimitBytes": item.quota_bytes,
            }
            for item in tenants
        ],
        "memberships": [
            {
                "tenantId": str(item.tenant_id),
                "actorId": str(item.user_id),
                "role": item.role,
                "isActive": item.is_active,
            }
            for item in members
        ],
        "uploads": [
            {
                "id": str(item.id),
                "tenantId": str(item.tenant_id),
                "versionId": str(item.document_version_id) if item.document_version_id else None,
                "filename": item.original_filename,
                "status": item.status,
                "sizeBytes": item.size_bytes,
                "sha256": item.declared_sha256,
            }
            for item in uploads
        ],
        "versions": [
            {
                "id": str(item.id),
                "tenantId": str(item.tenant_id),
                "documentId": str(item.document_id),
                "filename": item.original_filename,
                "status": item.status,
                "sha256": item.declared_sha256,
                "objectSha256": object_hashes[str(item.id)],
                "contentSha256Verified": item.content_sha256_verified_at is not None,
            }
            for item in versions
        ],
        "generations": [
            {
                "id": str(item.id),
                "versionId": str(item.document_version_id),
                "status": item.status,
                "stage": item.stage,
                "active": item.active,
                "chunkCount": item.chunk_count,
                "embeddedCount": item.embedded_count,
                "errorCode": item.error_code,
            }
            for item in generations
        ],
        "chunks": [
            {
                "id": str(item.id),
                "versionId": str(item.document_version_id),
                "text": item.normalized_text,
                "heading": item.heading,
                "pageNumber": item.page_number,
                "embeddingPresent": item.embedding is not None,
            }
            for item in chunks
        ],
        "jobs": [
            {
                "id": str(item.id),
                "tenantId": str(item.tenant_id),
                "versionId": str(item.document_version_id),
                "status": item.status,
                "attempts": item.attempts,
                "errorCode": item.last_error_code,
            }
            for item in jobs
        ],
        "jobAttempts": [
            {
                "jobId": str(item.job_id),
                "status": item.status,
                "workerId": item.worker_id,
                "errorCode": item.error_code,
            }
            for item in job_attempts
        ],
        "outbox": [
            {
                "id": str(item.id),
                "tenantId": str(item.tenant_id),
                "jobId": str(item.aggregate_id),
                "status": item.status,
                "attempts": item.attempts,
            }
            for item in outbox
        ],
        "claimedEventIds": [
            value for store in harness.stores.values() for value in store.claimed_ids
        ],
        "publisherRunning": bool(harness.publisher_tasks),
        "packets": [
            {"id": str(item.id), "tenantId": str(item.tenant_id), "actorId": str(item.actor_id)}
            for item in packets
        ],
        "presalesAttempts": [
            {
                "id": str(item.id),
                "tenantId": str(item.tenant_id),
                "rowId": str(item.row_id),
                "number": item.number,
                "state": item.state,
                "errorCode": item.error_code,
                "providerRequestCount": item.provider_request_count,
            }
            for item in attempts
        ],
        "reviews": [
            {
                "tenantId": str(item.tenant_id),
                "rowId": str(item.row_id),
                "actorId": str(item.actor_id),
                "revision": item.revision,
            }
            for item in reviews
        ],
        "entitlements": [
            {
                "tenantId": str(item.tenant_id),
                "limit": item.provider_request_limit,
                "used": item.provider_requests_used,
                "reserved": item.provider_requests_reserved,
            }
            for item in entitlements
        ],
        "reservations": [
            {
                "tenantId": str(item.tenant_id),
                "operationId": str(item.operation_id),
                "state": item.state,
            }
            for item in reservations
        ],
        "usageEvents": [
            {
                "tenantId": str(item.tenant_id),
                "operationId": str(item.operation_id),
                "eventType": item.event_type,
                "quantity": item.quantity,
                "estimatedCost": str(item.estimated_cost)
                if item.estimated_cost is not None
                else None,
                "currency": item.currency,
            }
            for item in events
        ],
        "audit": [
            {
                "tenantId": str(item.tenant_id) if item.tenant_id else None,
                "action": item.action,
                "requestLinked": item.request_id is not None,
            }
            for item in audits
        ],
        "modelRequests": harness.model_requests,
        "modelSuccesses": harness.model.calls,
    }
