from __future__ import annotations

from typing import cast

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.uploads.models import UploadSession


async def insert_upload_reservation(
    database: AsyncSession, *, tenant: Tenant, upload: UploadSession
) -> UploadSession:
    """Write the validated reservation while the caller holds identity/quota locks.

    The caller owns all authorization, policy, idempotency and quota checks. The
    inserted row depends on the quota UPDATE's returned tenant ID; either both
    writes commit or both roll back. Commit-acknowledgment recovery stays with the
    creation service. Synchronize the loaded tenant without a second ORM UPDATE.
    """
    await database.flush()
    reserved = tenant.reserved_storage_bytes + upload.size_bytes
    quota = (
        update(Tenant)
        .where(Tenant.id == tenant.id)
        .values(reserved_storage_bytes=reserved)
        .returning(Tenant.id, Tenant.updated_at)
        .cte("reserved_upload_quota")
    )
    statement = (
        insert(UploadSession)
        .values(
            id=upload.id,
            tenant_id=select(quota.c.id).scalar_subquery(),
            actor_id=upload.actor_id,
            pending_document_id=upload.pending_document_id,
            pending_version_id=upload.pending_version_id,
            status=upload.status,
            transport=upload.transport,
            idempotency_key=upload.idempotency_key,
            request_fingerprint=upload.request_fingerprint,
            object_key=upload.object_key,
            original_filename=upload.original_filename,
            extension=upload.extension,
            declared_media_type=upload.declared_media_type,
            size_bytes=upload.size_bytes,
            declared_sha256=upload.declared_sha256,
            part_size_bytes=upload.part_size_bytes,
            expected_part_count=upload.expected_part_count,
            reserved_bytes=upload.reserved_bytes,
            expires_at=upload.expires_at,
        )
        .returning(UploadSession, select(quota.c.updated_at).scalar_subquery())
    )
    created, updated_at = (await database.execute(statement)).one()
    set_committed_value(tenant, "reserved_storage_bytes", reserved)
    set_committed_value(tenant, "updated_at", updated_at)
    return cast(UploadSession, created)
