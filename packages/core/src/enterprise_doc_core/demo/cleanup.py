"""Retire expired demos, then reclaim exactly their proven objects and rows."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.models import AgentRun
from enterprise_doc_core.demo.models import DemoDay, DemoWorkspace
from enterprise_doc_core.demo.settings import CLEANUP_GRACE_SECONDS
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.object_store import (
    MultipartObjectStore,
    MultipartUploadNotFound,
    ObjectStoreNotFound,
)
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.policy import build_object_key

_LOGGER = logging.getLogger(__name__)


class DemoCleanupService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: MultipartObjectStore,
        documents_bucket: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sessions = session_factory
        self.object_store = object_store
        self.bucket = documents_bucket
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self) -> int:
        now = self.clock()
        async with self.sessions() as session:
            ids = list(
                await session.scalars(
                    select(DemoWorkspace.id)
                    .where(
                        DemoWorkspace.cleaned_at.is_(None),
                        or_(DemoWorkspace.expires_at <= now, DemoWorkspace.revoked_at.is_not(None)),
                    )
                    .order_by(DemoWorkspace.expires_at)
                    .limit(20)
                )
            )
        cleaned = 0
        for workspace_id in ids:
            try:
                async with asyncio.timeout(90):
                    cleaned += int(await self._clean(workspace_id, now))
            except Exception as error:
                # Keep the workspace and object ownership records for a retry.
                _LOGGER.warning(
                    "demo_cleanup_retry",
                    extra={
                        "event_data": {
                            "workspace_id": str(workspace_id),
                            "error_type": type(error).__name__,
                        }
                    },
                )
        async with self.sessions.begin() as session:
            await session.execute(
                delete(DemoWorkspace).where(DemoWorkspace.cleaned_at < now - timedelta(days=7))
            )
            await session.execute(
                delete(DemoDay).where(DemoDay.day < now.date() - timedelta(days=7))
            )
        return cleaned

    async def _clean(self, workspace_id: UUID, now: datetime) -> bool:
        # First retire authorization, independently of subsequent object-store failure.
        async with self.sessions.begin() as session:
            workspace = await session.get(DemoWorkspace, workspace_id)
            if workspace is None or workspace.tenant_id is None or workspace.cleaned_at is not None:
                return False
            await session.execute(
                update(Tenant).where(Tenant.id == workspace.tenant_id).values(is_active=False)
            )
            await session.execute(
                update(Membership)
                .where(Membership.tenant_id == workspace.tenant_id)
                .values(is_active=False)
            )
            await session.execute(
                update(Job)
                .where(
                    Job.tenant_id == workspace.tenant_id,
                    Job.status.in_(["pending", "retry_wait", "running"]),
                )
                .values(cancel_requested_at=now)
            )
        async with self.sessions.begin() as session:
            # A row lock makes duplicate cleaners safe across API replicas.
            workspace = await session.scalar(
                select(DemoWorkspace)
                .where(
                    DemoWorkspace.id == workspace_id,
                    DemoWorkspace.cleaned_at.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if workspace is None or workspace.tenant_id is None or workspace.actor_id is None:
                return False
            retired = min(workspace.expires_at, workspace.revoked_at or workspace.expires_at)
            if retired + timedelta(seconds=CLEANUP_GRACE_SECONDS) > now:
                return False
            if workspace.busy_until is not None and workspace.busy_until > now:
                return False
            tenant_id, actor_id = workspace.tenant_id, workspace.actor_id
            jobs = list(
                await session.scalars(
                    select(Job).where(Job.tenant_id == tenant_id).with_for_update()
                )
            )
            if any(job.lease_expires_at is not None and job.lease_expires_at > now for job in jobs):
                return False
            actor = await session.get(User, actor_id)
            # Do not destroy a workspace that an operator has connected to a real
            # identity or expanded beyond the demo capability boundary.
            bindings = await session.scalar(
                select(func.count())
                .select_from(ExternalIdentityBinding)
                .where(
                    or_(
                        ExternalIdentityBinding.user_id == actor_id,
                        ExternalIdentityBinding.tenant_id == tenant_id,
                    ),
                )
            )
            other_memberships = await session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    or_(
                        (Membership.user_id == actor_id) & (Membership.tenant_id != tenant_id),
                        (Membership.tenant_id == tenant_id) & (Membership.user_id != actor_id),
                    ),
                )
            )
            agents = await session.scalar(
                select(func.count()).select_from(AgentRun).where(AgentRun.tenant_id == tenant_id)
            )
            if (
                actor is None
                or actor.email != f"guest-{actor_id.hex}@demo.invalid"
                or bindings
                or other_memberships
                or agents
            ):
                raise RuntimeError("demo ownership changed")
            uploads = list(
                await session.scalars(
                    select(UploadSession).where(UploadSession.tenant_id == tenant_id)
                )
            )
            versions = list(
                await session.scalars(
                    select(DocumentVersion).where(DocumentVersion.tenant_id == tenant_id)
                )
            )
            known_uploads = {upload.id: upload for upload in uploads}
            for version in versions:
                source = known_uploads.get(version.upload_session_id)
                if (
                    source is None
                    or source.pending_version_id != version.id
                    or source.object_key != version.object_key
                ):
                    raise RuntimeError("demo document ownership mismatch")
            for upload in uploads:
                expected = build_object_key(
                    session_id=upload.id, version_id=upload.pending_version_id
                )
                if upload.object_key != expected or upload.actor_id != actor_id:
                    raise RuntimeError("demo upload ownership mismatch")
                if upload.object_store_upload_id:
                    try:
                        await self.object_store.abort_upload(
                            bucket=self.bucket,
                            key=expected,
                            upload_id=upload.object_store_upload_id,
                        )
                    except MultipartUploadNotFound:
                        pass
                try:
                    head = await self.object_store.head_object(bucket=self.bucket, key=expected)
                except ObjectStoreNotFound:
                    continue
                if any(
                    head.metadata.get(key) != value
                    for key, value in {
                        "contract": "m1",
                        "upload-session-id": str(upload.id),
                        "version-id": str(upload.pending_version_id),
                        "declared-size": str(upload.size_bytes),
                    }.items()
                ):
                    raise RuntimeError("demo object ownership mismatch")
                await self.object_store.delete_object(bucket=self.bucket, key=expected)
            # Break the existing upload/version RESTRICT cycle explicitly.
            await session.execute(
                update(UploadSession)
                .where(UploadSession.tenant_id == tenant_id)
                .values(document_version_id=None)
            )
            await session.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await session.execute(delete(UploadSession).where(UploadSession.tenant_id == tenant_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
            workspace.tenant_id = None
            workspace.actor_id = None
            workspace.credential_digest = None
            workspace.cleaned_at = now
            workspace.active_attempt_id = None
            workspace.busy_until = None
            return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as error:
                _LOGGER.warning(
                    "demo_cleanup_cycle_failed",
                    extra={"event_data": {"error_type": type(error).__name__}},
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)
            except TimeoutError:
                pass
