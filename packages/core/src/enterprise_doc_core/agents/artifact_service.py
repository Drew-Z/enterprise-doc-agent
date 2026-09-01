from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentRun,
    AgentRunTaskType,
)
from enterprise_doc_core.agents.schemas import BehaviorVersions, RiskHint
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.documents.policy import document_visible_to_actor
from enterprise_doc_core.documents.retrieval import ResolvedCitation
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.object_store import (
    ArtifactObjectStore,
    ObjectStoreChecksumMismatch,
    ObjectStoreError,
    ObjectStoreProtocolError,
)
from enterprise_doc_core.telemetry import MetricsRuntime


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


@dataclass(frozen=True, slots=True)
class AgentArtifactPreviewResult:
    artifact_id: UUID
    run_id: UUID
    document_version_id: UUID
    status: str
    content_sha256: str
    schema_version: int
    task_type: str
    answer_text: str
    structured_fields: dict[str, JsonValue] | None
    risk_hint: str | None
    citations: tuple[ResolvedCitation, ...]
    behavior_versions: BehaviorVersions


class _ArtifactCitationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_version_id: UUID
    source_filename: str | None = Field(default=None, max_length=255)
    page_number: int | None = Field(default=None, ge=1)
    heading: str | None = Field(default=None, max_length=500)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    excerpt: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_offsets(self) -> _ArtifactCitationPayload:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return self


class _AgentArtifactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    run_id: UUID
    task_type: AgentRunTaskType
    answer_text: str = Field(min_length=1, max_length=100_000)
    structured_fields: dict[str, JsonValue] | None
    risk_hint: RiskHint | None
    citations: list[_ArtifactCitationPayload] = Field(min_length=1, max_length=50)
    behavior_versions: BehaviorVersions


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
        metrics: MetricsRuntime | None = None,
    ) -> None:
        if not 1 <= download_ttl_seconds <= 3600:
            raise ValueError("download_ttl_seconds must be between 1 and 3600")
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.clock = clock
        self.download_ttl_seconds = download_ttl_seconds
        self.metrics = metrics

    async def list_for_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentArtifactResult, ...]:
        started = perf_counter()
        result_label = "error"
        try:
            result = await self._list_for_run(
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_id=run_id,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except AgentArtifactNotFound:
            result_label = "not_found"
            raise
        except AgentArtifactPrincipalForbidden:
            result_label = "forbidden"
            raise
        except AgentArtifactError:
            result_label = "permanent_error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="artifact",
                    operation="list",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def _list_for_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> tuple[AgentArtifactResult, ...]:
        async with self.session_factory() as session:
            await self._require_membership(session, tenant_id=tenant_id, actor_id=actor_id)
            run = await session.scalar(
                select(AgentRun)
                .join(DocumentVersion, DocumentVersion.id == AgentRun.document_version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.tenant_id == tenant_id,
                    document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                )
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
        started = perf_counter()
        result_label = "error"
        try:
            result = await self._get_download(
                tenant_id=tenant_id,
                actor_id=actor_id,
                artifact_id=artifact_id,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except AgentArtifactNotFound:
            result_label = "not_found"
            raise
        except AgentArtifactPrincipalForbidden:
            result_label = "forbidden"
            raise
        except AgentArtifactStoreUnavailable:
            result_label = "retryable_error"
            raise
        except AgentArtifactError:
            result_label = "permanent_error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="artifact",
                    operation="download",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def get_preview(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
    ) -> AgentArtifactPreviewResult:
        started = perf_counter()
        result_label = "error"
        try:
            result = await self._get_preview(
                tenant_id=tenant_id,
                actor_id=actor_id,
                artifact_id=artifact_id,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except AgentArtifactNotFound:
            result_label = "not_found"
            raise
        except AgentArtifactPrincipalForbidden:
            result_label = "forbidden"
            raise
        except AgentArtifactStoreUnavailable:
            result_label = "retryable_error"
            raise
        except AgentArtifactError:
            result_label = "permanent_error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="artifact",
                    operation="read",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def _get_download(
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
                .join(DocumentVersion, DocumentVersion.id == AgentRun.document_version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == tenant_id,
                    AgentRun.tenant_id == tenant_id,
                    document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
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
            except (ObjectStoreChecksumMismatch, ObjectStoreProtocolError) as error:
                raise AgentArtifactIntegrityError() from error
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

    async def _get_preview(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        artifact_id: UUID,
    ) -> AgentArtifactPreviewResult:
        async with self.session_factory() as session:
            await self._require_membership(session, tenant_id=tenant_id, actor_id=actor_id)
            row = await session.execute(
                select(AgentArtifact, AgentRun)
                .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
                .join(DocumentVersion, DocumentVersion.id == AgentRun.document_version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == tenant_id,
                    AgentRun.tenant_id == tenant_id,
                    document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                )
            )
            pair = row.one_or_none()
            if pair is None:
                raise AgentArtifactNotFound()
            artifact, run = pair
            if not self._is_visible(artifact=artifact, run=run):
                raise AgentArtifactNotFound()
            if artifact.content_type != "application/json":
                raise AgentArtifactIntegrityError()
            try:
                head = await self.artifact_store.head_object(
                    bucket=artifact.object_bucket,
                    key=artifact.object_key,
                )
                self._verify(artifact, size_bytes=head.size_bytes, metadata=dict(head.metadata))
                body = await self.artifact_store.read_object(
                    bucket=artifact.object_bucket,
                    key=artifact.object_key,
                    expected_size=head.size_bytes,
                )
            except AgentArtifactError:
                raise
            except (ObjectStoreChecksumMismatch, ObjectStoreProtocolError) as error:
                raise AgentArtifactIntegrityError() from error
            except ObjectStoreError as error:
                raise AgentArtifactStoreUnavailable() from error
            if hashlib.sha256(body).hexdigest() != artifact.content_sha256:
                raise AgentArtifactIntegrityError()
            return _parse_preview_payload(
                body,
                artifact_id=artifact.id,
                expected_run_id=run.id,
                expected_document_version_id=artifact.source_document_version_id,
                status=artifact.status,
                content_sha256=artifact.content_sha256 or "",
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


def _parse_preview_payload(
    body: bytes,
    *,
    artifact_id: UUID,
    expected_run_id: UUID,
    expected_document_version_id: UUID,
    status: str,
    content_sha256: str,
) -> AgentArtifactPreviewResult:
    try:
        payload = _AgentArtifactPayload.model_validate_json(body)
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise AgentArtifactIntegrityError() from error
    if payload.run_id != expected_run_id or any(
        citation.document_version_id != expected_document_version_id
        for citation in payload.citations
    ):
        raise AgentArtifactIntegrityError()
    return AgentArtifactPreviewResult(
        artifact_id=artifact_id,
        run_id=payload.run_id,
        document_version_id=expected_document_version_id,
        status=status,
        content_sha256=content_sha256,
        schema_version=payload.schema_version,
        task_type=payload.task_type.value,
        answer_text=payload.answer_text,
        structured_fields=payload.structured_fields,
        risk_hint=payload.risk_hint.value if payload.risk_hint is not None else None,
        citations=tuple(
            ResolvedCitation(
                chunk_id=citation.chunk_id,
                document_version_id=citation.document_version_id,
                source_filename=citation.source_filename,
                page_number=citation.page_number,
                heading=citation.heading,
                start_offset=citation.start_offset,
                end_offset=citation.end_offset,
                excerpt=citation.excerpt,
            )
            for citation in payload.citations
        ),
        behavior_versions=payload.behavior_versions,
    )


__all__ = [
    "AgentArtifactDownloadResult",
    "AgentArtifactError",
    "AgentArtifactIntegrityError",
    "AgentArtifactNotFound",
    "AgentArtifactPreviewResult",
    "AgentArtifactPrincipalForbidden",
    "AgentArtifactResult",
    "AgentArtifactService",
    "AgentArtifactStoreUnavailable",
]
