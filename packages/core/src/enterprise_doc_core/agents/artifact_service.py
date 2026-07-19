from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentRun,
)
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.object_store import (
    ArtifactObjectStore,
    ObjectStoreError,
)


class AgentArtifactError(Exception):
    code = "agent_artifact_error"
    message = "The Agent artifact request could not be completed."

    def __init__(self) -> None:
        super().__init__(self.message)


class AgentArtifactNotFound(AgentArtifactError):
    code = "agent_artifact_not_found"
    message = "The Agent artifact was not found."


class AgentArtifactPrincipalForbidden(AgentArtifactError):
    code = "agent_artifact_principal_forbidden"
    message = "An active tenant membership is required."


class AgentArtifactIntegrityError(AgentArtifactError):
    code = "agent_artifact_integrity_error"
    message = "The Agent artifact does not match its stored metadata."


class AgentArtifactStoreUnavailable(AgentArtifactError):
    code = "agent_artifact_store_unavailable"
    message = "The Agent artifact store is unavailable."


@dataclass(frozen=True, slots=True)
class AgentArtifactResult:
    artifact_id: UUID
    run_id: UUID
    document_version_id: UUID
    kind: str
    status: str
    content_type: str
    content_sha256: str
    size_bytes: int
    created_at: datetime
    verified_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentArtifactDownloadResult:
    artifact_id: UUID
    status: str
    content_type: str
    content_sha256: str
    size_bytes: int
    url: str
    expires_in_seconds: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentArtifactService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        artifact_store: ArtifactObjectStore,
        clock: Callable[[], datetime] = _utcnow,
        download_ttl_seconds: int = 300,
    ) -> None:
        if not 1 <= download_ttl_seconds <= 3600:
            raise ValueError("download_ttl_seconds must be between 1 and 3600")
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.clock = clock
        self.download_ttl_seconds = download_ttl_seconds

    async def list_for_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentArtifactResult, ...]:
        async with self.session_factory() as session:
            await self._require_membership(session, tenant_id=tenant_id, actor_id=actor_id)
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
            )
            if run is None:
                raise AgentArtifactNotFound()
            visible_statuses = (
                (AgentArtifactStatus.PUBLISHED.value,)
                if run.publish_requested
                else (AgentArtifactStatus.DRAFT_READY.value, AgentArtifactStatus.PUBLISHED.value)
            )
            artifacts = tuple(
                (
                    await session.scalars(
                        select(AgentArtifact)
                        .where(
                            AgentArtifact.tenant_id == tenant_id,
                            AgentArtifact.run_id == run_id,
                            AgentArtifact.status.in_(visible_statuses),
                        )
                        .order_by(AgentArtifact.created_at.asc(), AgentArtifact.id.asc())
                    )
                ).all()
            )
            return tuple(self._result(artifact) for artifact in artifacts)

    async def get_download(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
    ) -> AgentArtifactDownloadResult:
        async with self.session_factory() as session:
            await self._require_membership(session, tenant_id=tenant_id, actor_id=actor_id)
            row = await session.execute(
                select(AgentArtifact, AgentRun)
                .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
                .where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == tenant_id,
                    AgentRun.tenant_id == tenant_id,
                )
            )
            pair = row.one_or_none()
            if pair is None:
                raise AgentArtifactNotFound()
            artifact, run = pair
            if not self._is_visible(artifact=artifact, run=run):
                raise AgentArtifactNotFound()
            try:
                head = await self.artifact_store.head_object(
                    bucket=artifact.object_bucket,
                    key=artifact.object_key,
                )
                self._verify(artifact, size_bytes=head.size_bytes, metadata=dict(head.metadata))
                signed = await self.artifact_store.presign_get(
                    bucket=artifact.object_bucket,
                    key=artifact.object_key,
                    expires_in_seconds=self.download_ttl_seconds,
                )
            except AgentArtifactError:
                raise
            except ObjectStoreError as error:
                raise AgentArtifactStoreUnavailable() from error
            return AgentArtifactDownloadResult(
                artifact_id=artifact.id,
                status=artifact.status,
                content_type=artifact.content_type,
                content_sha256=artifact.content_sha256 or "",
                size_bytes=artifact.size_bytes or 0,
                url=signed.url,
                expires_in_seconds=signed.expires_in_seconds,
            )

    @staticmethod
    async def _require_membership(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> None:
        membership_id = await session.scalar(
            select(Membership.id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        if membership_id is None:
            raise AgentArtifactPrincipalForbidden()

    @staticmethod
    def _is_visible(*, artifact: AgentArtifact, run: AgentRun) -> bool:
        if run.publish_requested:
            return artifact.status == AgentArtifactStatus.PUBLISHED.value
        return artifact.status in {
            AgentArtifactStatus.DRAFT_READY.value,
            AgentArtifactStatus.PUBLISHED.value,
        }

    @staticmethod
    def _verify(
        artifact: AgentArtifact,
        *,
        size_bytes: int,
        metadata: dict[str, str],
    ) -> None:
        if (
            artifact.content_sha256 is None
            or artifact.size_bytes is None
            or artifact.verified_at is None
            or artifact.size_bytes != size_bytes
            or metadata.get("sha256") != artifact.content_sha256
        ):
            raise AgentArtifactIntegrityError()

    @staticmethod
    def _result(artifact: AgentArtifact) -> AgentArtifactResult:
        if (
            artifact.content_sha256 is None
            or artifact.size_bytes is None
            or artifact.verified_at is None
        ):
            raise AgentArtifactIntegrityError()
        return AgentArtifactResult(
            artifact_id=artifact.id,
            run_id=artifact.run_id,
            document_version_id=artifact.source_document_version_id,
            kind=artifact.kind,
            status=artifact.status,
            content_type=artifact.content_type,
            content_sha256=artifact.content_sha256,
            size_bytes=artifact.size_bytes,
            created_at=artifact.created_at,
            verified_at=artifact.verified_at,
            published_at=artifact.published_at,
        )


__all__ = [
    "AgentArtifactDownloadResult",
    "AgentArtifactError",
    "AgentArtifactIntegrityError",
    "AgentArtifactNotFound",
    "AgentArtifactPrincipalForbidden",
    "AgentArtifactResult",
    "AgentArtifactService",
    "AgentArtifactStoreUnavailable",
]
