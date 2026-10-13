from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.analysis.models import (
    AnalysisProject,
    AnalysisSnapshotSource,
    AnalysisSourceSnapshot,
)
from enterprise_doc_core.analysis.schemas import (
    AnalysisSpec,
    FrozenSource,
    ProjectView,
    SnapshotPayload,
    SnapshotView,
    SourceBlocksView,
    canonical_hash,
)
from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.documents.ingestion import DocumentParseViolation
from enterprise_doc_core.documents.models import (
    Document,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.policy import document_visible_to_actor
from enterprise_doc_core.documents.structure_store import load_structure, structure_digest
from enterprise_doc_core.identity.models import Membership, Tenant, User


class AnalysisError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AnalysisWorkspaceService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_project(
        self, *, tenant_id: UUID, actor_id: UUID, title: str, spec: AnalysisSpec
    ) -> ProjectView:
        if not title.strip() or len(title.strip()) > 160:
            raise AnalysisError("analysis_title_invalid")
        async with self.session_factory.begin() as session:
            await self._require_member(session, tenant_id, actor_id)
            await self._authorize_sources(session, tenant_id, actor_id, spec.source_ids())
            row = AnalysisProject(
                tenant_id=tenant_id,
                owner_id=actor_id,
                title=title.strip(),
                draft_spec=spec.model_dump(mode="json"),
            )
            session.add(row)
            await session.flush()
            await self._audit(session, tenant_id, actor_id, "analysis.project.created", row.id)
            return self._project_view(row)

    async def list_projects(self, *, tenant_id: UUID, actor_id: UUID) -> tuple[ProjectView, ...]:
        async with self.session_factory() as session:
            await self._require_member(session, tenant_id, actor_id)
            rows = (
                await session.scalars(
                    select(AnalysisProject)
                    .where(
                        AnalysisProject.tenant_id == tenant_id,
                        AnalysisProject.owner_id == actor_id,
                    )
                    .order_by(AnalysisProject.updated_at.desc(), AnalysisProject.id.desc())
                    .limit(100)
                )
            ).all()
            views = []
            for row in rows:
                view = self._project_view(row)
                try:
                    await self._authorize_sources(
                        session, tenant_id, actor_id, view.spec.source_ids()
                    )
                except AnalysisError as error:
                    if error.code != "analysis_not_found":
                        raise
                    continue
                views.append(view)
            return tuple(views)

    async def get_project(
        self, *, tenant_id: UUID, actor_id: UUID, project_id: UUID
    ) -> ProjectView:
        async with self.session_factory() as session:
            row = await self._project(session, tenant_id, actor_id, project_id)
            view = self._project_view(row)
            await self._authorize_sources(session, tenant_id, actor_id, view.spec.source_ids())
            return view

    async def update_project(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        project_id: UUID,
        expected_revision: int,
        title: str,
        spec: AnalysisSpec,
    ) -> ProjectView:
        if not title.strip() or len(title.strip()) > 160:
            raise AnalysisError("analysis_title_invalid")
        async with self.session_factory.begin() as session:
            row = await self._project(session, tenant_id, actor_id, project_id, lock=True)
            if row.draft_revision != expected_revision:
                raise AnalysisError("analysis_revision_conflict")
            # Check both scopes so an old revoked draft cannot be used as an oracle.
            await self._authorize_sources(
                session,
                tenant_id,
                actor_id,
                AnalysisSpec.model_validate(row.draft_spec).source_ids(),
            )
            await self._authorize_sources(session, tenant_id, actor_id, spec.source_ids())
            row.title = title.strip()
            row.draft_spec = spec.model_dump(mode="json")
            row.draft_revision += 1
            await session.flush()
            await self._audit(session, tenant_id, actor_id, "analysis.project.updated", row.id)
            return self._project_view(row)

    async def freeze_sources(
        self, *, tenant_id: UUID, actor_id: UUID, project_id: UUID, expected_revision: int
    ) -> SnapshotView:
        async with self.session_factory.begin() as session:
            project = await self._project(session, tenant_id, actor_id, project_id, lock=True)
            if project.draft_revision != expected_revision:
                raise AnalysisError("analysis_revision_conflict")
            spec = AnalysisSpec.model_validate(project.draft_spec)
            await self._authorize_sources(session, tenant_id, actor_id, spec.source_ids())
            previous = await session.scalar(
                select(AnalysisSourceSnapshot).where(
                    AnalysisSourceSnapshot.tenant_id == tenant_id,
                    AnalysisSourceSnapshot.project_id == project_id,
                    AnalysisSourceSnapshot.project_revision == expected_revision,
                )
            )
            if previous is not None:
                return await self._snapshot_view(session, previous, actor_id)
            if any(not item.source_version_ids for item in spec.objects):
                raise AnalysisError("analysis_sources_required")
            groups: dict[UUID, str | None] = {value: None for value in spec.common_requirements}
            groups.update(
                {value: item.id for item in spec.objects for value in item.source_version_ids}
            )
            sources = []
            for version_id, object_id in groups.items():
                result = (
                    await session.execute(
                        select(DocumentVersion, DocumentIngestionGeneration)
                        .join(
                            DocumentIngestionGeneration,
                            (DocumentIngestionGeneration.document_version_id == DocumentVersion.id)
                            & (DocumentIngestionGeneration.tenant_id == tenant_id),
                        )
                        .where(
                            DocumentVersion.id == version_id,
                            DocumentVersion.tenant_id == tenant_id,
                            DocumentVersion.status == "ready",
                            DocumentVersion.content_sha256_verified_at.is_not(None),
                            DocumentIngestionGeneration.active.is_(True),
                            DocumentIngestionGeneration.status == "succeeded",
                            DocumentIngestionGeneration.parser_version == 2,
                        )
                    )
                ).one_or_none()
                if result is None:
                    raise AnalysisError("analysis_source_not_ready")
                version, generation = result
                try:
                    parsed = await load_structure(
                        session,
                        tenant_id=tenant_id,
                        document_version_id=version.id,
                        generation_id=generation.id,
                        source_sha256=version.declared_sha256,
                    )
                except (DocumentParseViolation, ValidationError) as error:
                    raise AnalysisError("analysis_source_invalid") from error
                sources.append(
                    FrozenSource(
                        document_version_id=version.id,
                        generation_id=generation.id,
                        object_id=object_id,
                        filename=version.original_filename,
                        source_sha256=version.declared_sha256,
                        structure_sha256=structure_digest(parsed),
                        coverage=parsed.coverage,
                        block_count=len(parsed.blocks),
                        issues=parsed.issues,
                    )
                )
            payload = SnapshotPayload(
                schema_version=(
                    "analysis.sources.v1"
                    if spec.schema_version == "analysis.spec.v1"
                    else "analysis.sources.v2"
                ),
                title=project.title,
                spec=spec,
                sources=tuple(sources),
            )
            row = AnalysisSourceSnapshot(
                tenant_id=tenant_id,
                project_id=project_id,
                project_revision=expected_revision,
                source_manifest_hash=canonical_hash(payload),
                payload=payload.model_dump(mode="json"),
            )
            session.add(row)
            await session.flush()
            session.add_all(
                [
                    AnalysisSnapshotSource(
                        tenant_id=tenant_id,
                        snapshot_id=row.id,
                        document_version_id=source.document_version_id,
                        generation_id=source.generation_id,
                    )
                    for source in sources
                ]
            )
            await session.flush()
            await self._audit(session, tenant_id, actor_id, "analysis.sources.frozen", row.id)
            return await self._snapshot_view(session, row, actor_id)

    async def list_snapshots(
        self, *, tenant_id: UUID, actor_id: UUID, project_id: UUID
    ) -> tuple[SnapshotView, ...]:
        async with self.session_factory() as session:
            await self._project(session, tenant_id, actor_id, project_id)
            rows = (
                await session.scalars(
                    select(AnalysisSourceSnapshot)
                    .where(
                        AnalysisSourceSnapshot.tenant_id == tenant_id,
                        AnalysisSourceSnapshot.project_id == project_id,
                    )
                    .order_by(AnalysisSourceSnapshot.project_revision.desc())
                    .limit(100)
                )
            ).all()
            result = []
            for row in rows:
                try:
                    result.append(await self._snapshot_view(session, row, actor_id))
                except AnalysisError as error:
                    if error.code != "analysis_not_found":
                        raise
            return tuple(result)

    async def source_blocks(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        snapshot_id: UUID,
        document_version_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> SourceBlocksView:
        if offset < 0 or not 1 <= limit <= 100:
            raise AnalysisError("analysis_page_invalid")
        async with self.session_factory() as session:
            row = await session.scalar(
                select(AnalysisSourceSnapshot).where(
                    AnalysisSourceSnapshot.id == snapshot_id,
                    AnalysisSourceSnapshot.tenant_id == tenant_id,
                )
            )
            if row is None:
                raise AnalysisError("analysis_not_found")
            await self._project(session, tenant_id, actor_id, row.project_id)
            view = await self._snapshot_view(session, row, actor_id)
            source = next(
                (
                    value
                    for value in view.payload.sources
                    if value.document_version_id == document_version_id
                ),
                None,
            )
            if source is None:
                raise AnalysisError("analysis_not_found")
            try:
                parsed = await load_structure(
                    session,
                    tenant_id=tenant_id,
                    document_version_id=document_version_id,
                    generation_id=source.generation_id,
                    source_sha256=source.source_sha256,
                )
            except (DocumentParseViolation, ValidationError) as error:
                raise AnalysisError("analysis_source_invalid") from error
            if structure_digest(parsed) != source.structure_sha256:
                raise AnalysisError("analysis_source_invalid")
            end = min(offset + limit, len(parsed.blocks))
            return SourceBlocksView(
                source=source,
                blocks=parsed.blocks[offset:end],
                next_offset=end if end < len(parsed.blocks) else None,
            )

    async def _snapshot_view(
        self, session: AsyncSession, row: AnalysisSourceSnapshot, actor_id: UUID
    ) -> SnapshotView:
        try:
            payload = SnapshotPayload.model_validate(row.payload)
        except ValidationError as error:
            raise AnalysisError("analysis_source_invalid") from error
        if canonical_hash(row.payload) != row.source_manifest_hash:
            raise AnalysisError("analysis_source_invalid")
        await self._authorize_sources(session, row.tenant_id, actor_id, payload.spec.source_ids())
        return SnapshotView(
            id=row.id,
            project_id=row.project_id,
            project_revision=row.project_revision,
            source_manifest_hash=row.source_manifest_hash,
            payload=payload,
            created_at=row.created_at,
        )

    @staticmethod
    async def _require_member(session: AsyncSession, tenant_id: UUID, actor_id: UUID) -> None:
        member = await session.scalar(
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
        if member is None:
            raise AnalysisError("analysis_not_found")

    async def _project(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID,
        project_id: UUID,
        *,
        lock: bool = False,
    ) -> AnalysisProject:
        await self._require_member(session, tenant_id, actor_id)
        query = select(AnalysisProject).where(
            AnalysisProject.id == project_id,
            AnalysisProject.tenant_id == tenant_id,
            AnalysisProject.owner_id == actor_id,
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise AnalysisError("analysis_not_found")
        return row

    @staticmethod
    async def _authorize_sources(
        session: AsyncSession, tenant_id: UUID, actor_id: UUID, source_ids: tuple[UUID, ...]
    ) -> None:
        if not source_ids:
            return
        visible = set(
            (
                await session.scalars(
                    select(DocumentVersion.id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        DocumentVersion.id.in_(source_ids),
                        DocumentVersion.tenant_id == tenant_id,
                        document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                    )
                )
            ).all()
        )
        if visible != set(source_ids):
            raise AnalysisError("analysis_not_found")

    @staticmethod
    def _project_view(row: AnalysisProject) -> ProjectView:
        return ProjectView(
            id=row.id,
            title=row.title,
            revision=row.draft_revision,
            spec=AnalysisSpec.model_validate(row.draft_spec),
            created_at=row.created_at,
        )

    @staticmethod
    async def _audit(
        session: AsyncSession, tenant_id: UUID, actor_id: UUID, action: str, resource_id: UUID
    ) -> None:
        await append_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="analysis",
            resource_id=resource_id,
            metadata={},
        )
