from __future__ import annotations

import pytest
from sqlalchemy import text

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.uploads import UploadSessionService
from tests.multipart.test_upload_complete_integration import (
    CompletionObjectStore,
    _cleanup_seeded,
    _completion_request,
    _seed_upload,
)

pytestmark = pytest.mark.integration


async def test_completed_upload_replays_preserve_prepared_reads_and_single_quota_conversion():
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(
        settings.database.model_copy(update={"pool_size": 1, "max_overflow": 0})
    )
    sessions = create_session_factory(engine)
    seeded = await _seed_upload(sessions, content=b"%PDF-1.7")
    store = CompletionObjectStore(seeded)
    service = UploadSessionService(
        session_factory=sessions,
        object_store=store,
        documents_bucket=settings.object_store.documents_bucket,
        settings=settings.upload,
    )
    try:
        first = await service.complete(
            principal=seeded.principal.context,
            session_id=seeded.session_id,
            request=_completion_request(seeded.parts),
        )
        for _ in range(8):
            replay = await service.complete(
                principal=seeded.principal.context,
                session_id=seeded.session_id,
                request=_completion_request(seeded.parts),
            )
            assert replay.document_id == first.document_id
            assert replay.version_id == first.version_id
            assert replay.completed_at == first.completed_at
            assert replay.replayed is True
        assert store.complete_calls == 1
        async with sessions.begin() as session:
            queries = (
                await session.scalars(text("SELECT statement FROM pg_prepared_statements"))
            ).all()
            assert any("FROM upload_sessions" in query for query in queries)
            assert await session.scalar(text("SHOW transaction_read_only")) == "off"
            quota = (
                await session.execute(
                    text(
                        "SELECT used_storage_bytes, reserved_storage_bytes "
                        "FROM tenants WHERE id = :id"
                    ),
                    {"id": seeded.principal.tenant_id},
                )
            ).one()
            assert tuple(quota) == (len(seeded.content), 0)
    finally:
        await _cleanup_seeded(sessions, seeded)
        await engine.dispose()
