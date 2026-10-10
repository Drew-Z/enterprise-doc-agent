from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.documents import Document, DocumentVersion, DocumentVersionStatus
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.jobs.service import prepare_job_records
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus


async def insert_completion_records(
    database: AsyncSession,
    *,
    upload: UploadSession,
    size_bytes: int,
    detected_media_type: str,
    transport_checksum: str | None,
    content_sha256_verified_at: datetime | None,
    completed_at: datetime,
    ingestion_max_attempts: int,
) -> None:
    """Persist a validated completion while the caller holds Tenant then UploadSession.

    The caller owns all state/identity/quota checks and commit recovery. Loaded
    ORM rows remain clean; they must not be reused for mutable values after this
    Core statement. Every parent/child edge uses the inserted parent's RETURNING.
    """
    document = (
        insert(Document)
        .values(
            id=upload.pending_document_id,
            tenant_id=upload.tenant_id,
            created_by=upload.actor_id,
            title=upload.original_filename,
        )
        .returning(Document.id)
        .cte("completed_document")
    )
    version = (
        insert(DocumentVersion)
        .values(
            id=upload.pending_version_id,
            tenant_id=upload.tenant_id,
            document_id=select(document.c.id).scalar_subquery(),
            upload_session_id=upload.id,
            version_number=1,
            status=DocumentVersionStatus.UPLOADED.value,
            object_key=upload.object_key,
            original_filename=upload.original_filename,
            declared_media_type=upload.declared_media_type,
            detected_media_type=detected_media_type,
            size_bytes=size_bytes,
            declared_sha256=upload.declared_sha256,
            content_sha256_verified_at=content_sha256_verified_at,
            transport_checksum_sha256=transport_checksum,
            created_by=upload.actor_id,
        )
        .returning(DocumentVersion.id)
        .cte("completed_version")
    )
    version_id = select(version.c.id).scalar_subquery()
    job = await prepare_job_records(
        database,
        tenant_id=upload.tenant_id,
        actor_id=upload.actor_id,
        job_type="document.ingest",
        idempotency_key=f"document-version:{upload.pending_version_id}",
        payload={"document_version_id": str(upload.pending_version_id)},
        document_version_id=upload.pending_version_id,
        document_version_reference=version_id,
        max_attempts=ingestion_max_attempts,
        outbox_event_type="document.ingest.requested",
    )
    if job.statement is None:
        # A new completion cannot reuse a job whose version already exists. The
        # old path rejected its duplicate Document/Version insert as well.
        raise RuntimeError("new completion already has ingestion records")
    quota = (
        update(Tenant)
        .where(Tenant.id == upload.tenant_id)
        .values(
            reserved_storage_bytes=Tenant.reserved_storage_bytes - upload.reserved_bytes,
            used_storage_bytes=Tenant.used_storage_bytes + size_bytes,
        )
        .cte("completed_storage_quota")
    )
    completed_upload = (
        update(UploadSession)
        .where(UploadSession.id == upload.id)
        .values(
            reserved_bytes=0,
            document_version_id=version_id,
            status=UploadSessionStatus.COMPLETED.value,
            completed_at=completed_at,
            last_error_code=None,
            cleanup_claimed_at=None,
            cleanup_claim_token=None,
        )
        .cte("completed_upload_session")
    )
    await database.execute(job.statement.add_cte(quota, completed_upload))
