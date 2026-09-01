from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_core.audit import (
    AuditEvent,
    AuditGovernanceForbidden,
    AuditGovernanceService,
    append_audit_event,
)
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.object_store import ArtifactObject, ObjectHead, PresignedObjectDownload

pytestmark = pytest.mark.integration


class MemoryArchiveStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls = 0

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata=None,
    ) -> ArtifactObject:
        self.put_calls += 1
        self.objects[(bucket, key)] = body
        return ArtifactObject(
            bucket=bucket,
            key=key,
            size_bytes=len(body),
            content_sha256=hashlib.sha256(body).hexdigest(),
            content_type=content_type,
            etag="archive-etag",
        )

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        body = self.objects[(bucket, key)]
        return ObjectHead(
            size_bytes=len(body),
            etag="archive-etag",
            checksum_sha256_b64=None,
            content_type="application/json",
            metadata={"sha256": hashlib.sha256(body).hexdigest()},
        )

    async def read_object(self, *, bucket: str, key: str, expected_size: int) -> bytes:
        body = self.objects[(bucket, key)]
        assert len(body) == expected_size
        return body

    async def presign_get(
        self,
        *,
        bucket: str,
        key: str,
        expires_in_seconds: int,
    ) -> PresignedObjectDownload:
        assert (bucket, key) in self.objects
        return PresignedObjectDownload(
            url=f"https://archive.test/{key}?ttl={expires_in_seconds}",
            expires_in_seconds=expires_in_seconds,
        )


async def test_retention_preview_respects_resource_hold_and_live_release() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = AuditGovernanceService(session_factory=session_factory)
    other_resource_id = uuid4()
    old = datetime.now(UTC) - timedelta(days=60)
    try:
        async with session_factory.begin() as session:
            await append_audit_event(
                session,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                action="document.reviewed",
                resource_type="document",
                resource_id=context.document_id,
                occurred_at=old,
            )
            await append_audit_event(
                session,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                action="document.reviewed",
                resource_type="document",
                resource_id=other_resource_id,
                occurred_at=old,
            )
            await append_audit_event(
                session,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                action="document.reviewed",
                resource_type="document",
                resource_id=context.document_id,
            )

        policy = await service.set_retention_policy(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            retention_days=30,
            is_enabled=True,
            request_id="retention-request",
            correlation_id="retention-correlation",
        )
        assert policy.retention_days == 30
        assert policy.is_enabled is True

        before_hold = await service.retention_preview(tenant_id=context.tenant_id)
        assert before_hold.eligible_event_count == 2
        assert before_hold.protected_event_count == 0
        plan = await service.retention_plan(
            tenant_id=context.tenant_id,
            limit=1,
            now=datetime.now(UTC),
        )
        assert plan.eligible_event_count == 2
        assert len(plan.eligible_event_ids) == 1
        assert len(plan.fingerprint) == 64

        hold = await service.create_legal_hold(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            name="Contract review case",
            reason="Preserve document-scoped evidence",
            resource_type="document",
            resource_id=context.document_id,
            request_id="hold-request",
            correlation_id="hold-correlation",
        )
        with_hold = await service.retention_preview(tenant_id=context.tenant_id)
        assert with_hold.eligible_event_count == 1
        assert with_hold.protected_event_count == 1
        assert [
            item.hold_id for item in await service.list_legal_holds(tenant_id=context.tenant_id)
        ] == [hold.hold_id]

        released = await service.release_legal_hold(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            hold_id=hold.hold_id,
            request_id="release-request",
            correlation_id="release-correlation",
        )
        repeated = await service.release_legal_hold(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            hold_id=hold.hold_id,
        )
        assert released.released_at is not None
        assert repeated.released_at == released.released_at
        after_release = await service.retention_preview(tenant_id=context.tenant_id)
        assert after_release.eligible_event_count == 2
        assert after_release.protected_event_count == 0

        async with session_factory() as session:
            actions = tuple(
                await session.scalars(
                    select(AuditEvent.action).where(AuditEvent.tenant_id == context.tenant_id)
                )
            )
        assert actions.count("audit.retention_policy.updated") == 1
        assert actions.count("audit.legal_hold.created") == 1
        assert actions.count("audit.legal_hold.released") == 1
    finally:
        await engine.dispose()


async def test_audit_governance_mutations_require_owner_role() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = AuditGovernanceService(session_factory=session_factory)
    try:
        with pytest.raises(AuditGovernanceForbidden):
            await service.set_retention_policy(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="member",
                retention_days=365,
                is_enabled=True,
            )
        with pytest.raises(AuditGovernanceForbidden):
            await service.create_legal_hold(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="member",
                name="Forbidden",
                reason="Members cannot create holds",
            )
    finally:
        await engine.dispose()


async def test_retention_archive_writes_verified_snapshot_without_deleting_events() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    archive_store = MemoryArchiveStore()
    service = AuditGovernanceService(
        session_factory=session_factory,
        archive_store=archive_store,
        archive_bucket="audit-archive",
    )
    old = datetime.now(UTC) - timedelta(days=60)
    try:
        async with session_factory.begin() as session:
            await append_audit_event(
                session,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                action="document.reviewed",
                resource_type="document",
                resource_id=context.document_id,
                occurred_at=old,
            )
        await service.set_retention_policy(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            retention_days=30,
            is_enabled=True,
        )

        first = await service.archive_retention_plan(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            now=datetime.now(UTC),
        )
        second = await service.archive_retention_plan(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            now=datetime.now(UTC),
        )

        assert first.batch_id == second.batch_id
        assert first.content_sha256 == second.content_sha256
        assert archive_store.put_calls == 1
        assert len(archive_store.objects) == 1
        async with session_factory() as session:
            remaining = await session.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.tenant_id == context.tenant_id,
                    AuditEvent.action == "document.reviewed",
                )
            )
            archive_actions = tuple(
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.action == "audit.retention_archived",
                    )
                )
            )
        assert remaining is not None
        assert archive_actions == ("audit.retention_archived",)

        batches = await service.list_archive_batches(tenant_id=context.tenant_id)
        assert len(batches) == 1
        verification = await service.verify_archive_batch(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            batch_id=first.batch_id,
        )
        assert verification.valid is True
        assert verification.envelope_valid is True
        assert verification.actual_sha256 == first.content_sha256
        download = await service.get_archive_download(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            batch_id=first.batch_id,
            expires_in_seconds=120,
        )
        assert download.url.endswith("?ttl=120")
        assert download.content_sha256 == first.content_sha256
    finally:
        await engine.dispose()
