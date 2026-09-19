from __future__ import annotations

import hashlib
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents.models import (
    Document,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.policy import document_visible_to_actor
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.models import PresalesPacket, PresalesRow
from enterprise_doc_core.presales.schemas import SourceInput, SourceSnapshot


def fingerprint(payload: BaseModel) -> str:
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


def check_key(key: str) -> None:
    if not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise PresalesError("presales_invalid_idempotency_key")


async def authorize_principal(
    session: AsyncSession, principal: PrincipalContext, *, lock: bool = False
) -> tuple[UUID, UUID]:
    try:
        tenant_id, actor_id = UUID(principal.tenant_id), UUID(principal.actor_id)
    except ValueError as error:
        raise PresalesError("presales_forbidden") from error
    statement = (
        select(Tenant)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Tenant.id == tenant_id,
            Tenant.is_active.is_(True),
            Membership.user_id == actor_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
            Membership.role.in_(("owner", "member")),
        )
    )
    if lock:
        statement = statement.with_for_update(of=Tenant)
    if await session.scalar(statement) is None:
        raise PresalesError("presales_forbidden")
    return tenant_id, actor_id


async def source_snapshots(
    session: AsyncSession, tenant_id: UUID, actor_id: UUID, sources: list[SourceInput]
) -> list[SourceSnapshot]:
    result = []
    for source in sources:
        row = (
            await session.execute(
                select(DocumentVersion, DocumentIngestionGeneration)
                .join(Document, Document.id == DocumentVersion.document_id)
                .join(
                    DocumentIngestionGeneration,
                    DocumentIngestionGeneration.document_version_id == DocumentVersion.id,
                )
                .where(
                    DocumentVersion.id == source.version_id,
                    DocumentVersion.tenant_id == tenant_id,
                    document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                    DocumentVersion.status == "ready",
                    DocumentIngestionGeneration.tenant_id == tenant_id,
                    DocumentIngestionGeneration.active.is_(True),
                    DocumentIngestionGeneration.status == "succeeded",
                    DocumentIngestionGeneration.stage == "ready",
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            raise PresalesError("presales_source_unavailable")
        version, generation = row
        latest = await session.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == version.document_id,
                DocumentVersion.tenant_id == tenant_id,
            )
        )
        result.append(
            SourceSnapshot(
                version_id=version.id,
                document_id=version.document_id,
                generation_id=generation.id,
                filename=version.original_filename,
                version_number=version.version_number,
                latest_version_number=latest or version.version_number,
                content_sha256=version.declared_sha256,
                applicability=source.applicability,
            )
        )
    return result


async def check_sources(session: AsyncSession, packet: PresalesPacket) -> list[SourceSnapshot]:
    expected = [SourceSnapshot.model_validate(item) for item in packet.sources]
    actual = await source_snapshots(
        session,
        packet.tenant_id,
        packet.actor_id,
        [SourceInput(version_id=s.version_id, applicability=s.applicability) for s in expected],
    )
    if actual != expected:
        raise PresalesError("presales_stale_sources")
    return expected


async def load_packet(
    session: AsyncSession, principal: PrincipalContext, packet_id: UUID, *, lock: bool = False
) -> PresalesPacket:
    tenant_id, actor_id = await authorize_principal(session, principal, lock=lock)
    statement = select(PresalesPacket).where(
        PresalesPacket.id == packet_id,
        PresalesPacket.tenant_id == tenant_id,
        PresalesPacket.actor_id == actor_id,
    )
    if lock:
        statement = statement.with_for_update()
    packet = await session.scalar(statement)
    if packet is None:
        raise PresalesError("presales_not_found")
    await check_sources(session, packet)
    return packet


async def load_row(session: AsyncSession, packet: PresalesPacket, row_id: UUID) -> PresalesRow:
    row = await session.scalar(
        select(PresalesRow)
        .where(
            PresalesRow.id == row_id,
            PresalesRow.packet_id == packet.id,
            PresalesRow.tenant_id == packet.tenant_id,
        )
        .with_for_update()
    )
    if row is None:
        raise PresalesError("presales_not_found")
    return row
