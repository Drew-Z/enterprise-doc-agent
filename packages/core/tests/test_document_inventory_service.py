from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enterprise_doc_core.documents import (
    Document,
    DocumentIngestionGeneration,
    DocumentInventoryService,
    DocumentVersion,
)


class FakeResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self.rows


class FakeSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statement = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.rows)


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


async def test_inventory_returns_latest_generation_metadata_without_internal_content() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    document = Document(
        id=uuid4(),
        tenant_id=tenant_id,
        created_by=actor_id,
        title="Policy",
        access_mode="tenant",
    )
    version = DocumentVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        document_id=document.id,
        upload_session_id=uuid4(),
        version_number=2,
        status="failed",
        object_key="must-not-leak",
        original_filename="policy.pdf",
        declared_media_type="application/pdf",
        detected_media_type="application/pdf",
        size_bytes=1024,
        declared_sha256="a" * 64,
        created_by=actor_id,
    )
    generation = DocumentIngestionGeneration(
        id=uuid4(),
        tenant_id=tenant_id,
        document_version_id=version.id,
        status="failed",
        stage="embed",
        error_code="embedding_unavailable",
    )
    now = datetime(2026, 8, 23, tzinfo=UTC)
    version.created_at = now
    version.updated_at = now
    session = FakeSession([(document, version, generation)])
    service = DocumentInventoryService(session_factory=FakeSessionFactory(session))  # type: ignore[arg-type]

    result = await service.list_versions(tenant_id=tenant_id, limit=25)

    assert result[0].filename == "policy.pdf"
    assert result[0].access_mode == "tenant"
    assert result[0].version_number == 2
    assert result[0].ingestion_status == "failed"
    assert result[0].ingestion_stage == "embed"
    assert result[0].error_code == "embedding_unavailable"
    assert "object_key" not in result[0].__dataclass_fields__
    assert "declared_sha256" not in result[0].__dataclass_fields__
    compiled = session.statement.compile()  # type: ignore[union-attr]
    sql = str(compiled)
    assert "documents.tenant_id" in sql
    assert "document_versions.tenant_id" in sql


@pytest.mark.parametrize("limit", [0, 201])
async def test_inventory_rejects_unbounded_limits(limit: int) -> None:
    session = FakeSession([])
    service = DocumentInventoryService(session_factory=FakeSessionFactory(session))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="between 1 and 200"):
        await service.list_versions(tenant_id=uuid4(), limit=limit)

    assert session.statement is None
