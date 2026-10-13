from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from enterprise_doc_core.analysis.models import AnalysisSourceSnapshot
from enterprise_doc_core.analysis.schemas import AnalysisSpec, ComparisonObject, default_fields
from enterprise_doc_core.analysis.service import AnalysisError, AnalysisWorkspaceService
from enterprise_doc_core.documents.models import (
    Document,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.structure import parse_document_structure
from enterprise_doc_core.documents.structure_models import DocumentStructure
from enterprise_doc_core.documents.structure_store import persist_structure
from enterprise_doc_core.identity.models import Membership, User
from tests.jobs.test_m3_ingestion_integration import _seed_uploaded_document

pytestmark = pytest.mark.integration


async def seed_source(sessions):
    content = "实施费 1200 元\n交付时间 30 天, 收到首款后起算".encode()
    tenant, _owner, version_id, _ = await _seed_uploaded_document(sessions, content=content)
    actor = uuid4()
    async with sessions.begin() as session:
        session.add(User(id=actor, email=f"analysis-{actor}@example.test"))
        await session.flush()
        session.add(Membership(tenant_id=tenant, user_id=actor, role="member"))
        version = await session.get(DocumentVersion, version_id)
        version.status = "ready"
        version.content_sha256_verified_at = datetime.now(UTC)
        document_id = version.document_id
        generation = DocumentIngestionGeneration(
            tenant_id=tenant,
            document_version_id=version_id,
            parser_version=2,
            chunker_version=1,
            embedding_version=2,
            status="succeeded",
            stage="ready",
            active=True,
        )
        session.add(generation)
        await session.flush()
        await persist_structure(
            session,
            tenant_id=tenant,
            document_version_id=version_id,
            generation_id=generation.id,
            parsed=parse_document_structure(content, extension=".txt"),
        )
    return tenant, actor, version_id, document_id


async def test_source_snapshot_is_idempotent_immutable_and_rechecks_all_source_access(
    browser_db,
) -> None:
    tenant, actor, version, document_id = await seed_source(browser_db.sessions)
    service = AnalysisWorkspaceService(session_factory=browser_db.sessions)
    spec = AnalysisSpec(
        objects=(
            ComparisonObject(id="supplier_a", name="供应商 A", source_version_ids=(version,)),
        ),
        fields=default_fields(),
    )
    project = await service.create_project(
        tenant_id=tenant, actor_id=actor, title="方案对比", spec=spec
    )
    frozen = await service.freeze_sources(
        tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
    )
    replay = await service.freeze_sources(
        tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
    )
    assert replay.id == frozen.id
    blocks = await service.source_blocks(
        tenant_id=tenant,
        actor_id=actor,
        snapshot_id=frozen.id,
        document_version_id=version,
        limit=1,
    )
    assert blocks.blocks[0].text == "实施费 1200 元"
    assert blocks.blocks[0].locator.line == 1
    assert blocks.next_offset == 1
    updated = await service.update_project(
        tenant_id=tenant,
        actor_id=actor,
        project_id=project.id,
        expected_revision=1,
        title="修订项目",
        spec=spec,
    )
    assert updated.revision == 2
    with pytest.raises(AnalysisError, match="revision_conflict"):
        await service.update_project(
            tenant_id=tenant,
            actor_id=actor,
            project_id=project.id,
            expected_revision=1,
            title="失效标签",
            spec=spec,
        )
    history = await service.list_snapshots(tenant_id=tenant, actor_id=actor, project_id=project.id)
    assert history[0].payload.title == "方案对比"
    assert history[0].source_manifest_hash == frozen.source_manifest_hash
    with pytest.raises(AnalysisError, match="not_found"):
        await service.source_blocks(
            tenant_id=uuid4(), actor_id=actor, snapshot_id=frozen.id, document_version_id=version
        )
    async with browser_db.sessions.begin() as session:
        document = await session.get(Document, document_id)
        document.access_mode = "restricted"
    with pytest.raises(AnalysisError, match="not_found"):
        await service.source_blocks(
            tenant_id=tenant, actor_id=actor, snapshot_id=frozen.id, document_version_id=version
        )
    assert await service.list_projects(tenant_id=tenant, actor_id=actor) == ()
    assert (
        await service.list_snapshots(tenant_id=tenant, actor_id=actor, project_id=project.id) == ()
    )
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AnalysisSourceSnapshot)) == 1


async def test_source_tampering_and_legacy_generation_cannot_be_frozen(browser_db) -> None:
    tenant, actor, version, _ = await seed_source(browser_db.sessions)
    service = AnalysisWorkspaceService(session_factory=browser_db.sessions)
    spec = AnalysisSpec(
        objects=(ComparisonObject(id="a", name="A", source_version_ids=(version,)),),
        fields=default_fields(),
    )
    project = await service.create_project(
        tenant_id=tenant, actor_id=actor, title="报价", spec=spec
    )
    async with browser_db.sessions.begin() as session:
        generation = await session.scalar(select(DocumentIngestionGeneration))
        generation.parser_version = 1
    with pytest.raises(AnalysisError, match="source_not_ready"):
        await service.freeze_sources(
            tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
        )
    async with browser_db.sessions.begin() as session:
        generation = await session.scalar(select(DocumentIngestionGeneration))
        generation.parser_version = 2
        structure = await session.scalar(select(DocumentStructure))
        structure.payload_sha256 = "0" * 64
    with pytest.raises(AnalysisError, match="source_invalid"):
        await service.freeze_sources(
            tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
        )
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AnalysisSourceSnapshot)) == 0
