from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from enterprise_doc_core.analysis.schemas import AnalysisSpec, ComparisonObject, default_fields
from enterprise_doc_core.analysis.service import AnalysisWorkspaceService
from enterprise_doc_core.config import EmbeddingSettings, UploadSettings
from enterprise_doc_core.documents.models import DocumentIngestionGeneration
from enterprise_doc_core.jobs import JobRuntimeService
from enterprise_doc_core.jobs.models import Job, OutboxEvent
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.session_service import UploadSessionService
from enterprise_doc_worker.handler import build_consumer_factory
from enterprise_doc_worker.queue import JobMessage
from tests.multipart.test_upload_complete_integration import (
    CompletionObjectStore,
    _completion_request,
    _seed_upload,
)

pytestmark = pytest.mark.integration


async def test_xlsx_completion_to_real_worker_to_frozen_source_is_replay_safe(browser_db) -> None:
    workbook = Workbook()
    workbook.active.title = "报价"
    workbook.active.append(["实施费", 1200])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    seeded = await _seed_upload(browser_db.sessions, content=buffer.getvalue(), part_size=100)
    async with browser_db.sessions.begin() as session:
        upload = await session.get(UploadSession, seeded.session_id)
        upload.original_filename = "报价.xlsx"
        upload.extension = ".xlsx"
        upload.declared_media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    objects = CompletionObjectStore(seeded)
    service = UploadSessionService(
        session_factory=browser_db.sessions,
        object_store=objects,
        documents_bucket="documents",
        settings=UploadSettings(structured_documents_enabled=True),
    )
    complete = await service.complete(
        principal=seeded.principal.context,
        session_id=seeded.session_id,
        request=_completion_request(seeded.parts),
    )
    # A flag rollback must not reinterpret work that has already been admitted.
    service.settings = UploadSettings()
    replay = await service.complete(
        principal=seeded.principal.context,
        session_id=seeded.session_id,
        request=_completion_request(seeded.parts),
    )
    assert replay.version_id == complete.version_id and replay.replayed
    async with browser_db.sessions() as session:
        job = await session.scalar(select(Job))
        assert job.payload["parser_version"] == 2
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        job_id = job.id
    consumer = build_consumer_factory(
        runtime=JobRuntimeService(session_factory=browser_db.sessions),
        session_factory=browser_db.sessions,
        object_store=objects,
        documents_bucket="documents",
        worker_id="structured-integration",
        embedding_settings=EmbeddingSettings(),
    )()
    message = JobMessage(job_id=job_id, tenant_id=seeded.principal.tenant_id, event_id=uuid4())
    assert await consumer.handle(message) == "succeeded"
    assert await consumer.handle(message) == "duplicate_or_not_claimable"
    analysis = AnalysisWorkspaceService(session_factory=browser_db.sessions)
    identity = dict(tenant_id=seeded.principal.tenant_id, actor_id=seeded.principal.actor_id)
    project = await analysis.create_project(
        **identity,
        title="报价核对",
        spec=AnalysisSpec(
            objects=(
                ComparisonObject(id="a", name="A", source_version_ids=(complete.version_id,)),
            ),
            fields=default_fields(),
        ),
    )
    frozen = await analysis.freeze_sources(**identity, project_id=project.id, expected_revision=1)
    blocks = await analysis.source_blocks(
        **identity, snapshot_id=frozen.id, document_version_id=complete.version_id
    )
    assert blocks.blocks[1].text == "1200"
    assert blocks.blocks[1].locator.sheet == "报价"
    assert blocks.blocks[1].locator.cell == "B1"
    async with browser_db.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DocumentIngestionGeneration)) == 1
        )
