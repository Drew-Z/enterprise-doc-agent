from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import DocumentInventoryItemResult


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubDocumentInventoryService:
    def __init__(self) -> None:
        self.tenant_ids: list[UUID] = []
        self.version_id = uuid4()

    async def list_versions(self, *, tenant_id: UUID, actor_id: UUID, role: str, limit: int = 100):
        self.tenant_ids.append(tenant_id)
        assert actor_id
        assert role == "owner"
        assert limit == 25
        return (
            DocumentInventoryItemResult(
                document_id=uuid4(),
                title="Security policy",
                access_mode="tenant",
                can_manage=True,
                version_id=self.version_id,
                version_number=2,
                filename="security-policy.pdf",
                media_type="application/pdf",
                size_bytes=524_288,
                version_status="failed",
                generation_id=uuid4(),
                ingestion_status="failed",
                ingestion_stage="embed",
                error_code="embedding_provider_unavailable",
                created_at=datetime(2026, 8, 22, tzinfo=UTC),
                updated_at=datetime(2026, 8, 23, tzinfo=UTC),
            ),
        )


async def test_document_inventory_is_authenticated_tenant_scoped_and_typed() -> None:
    tenant_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    service = StubDocumentInventoryService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        document_inventory_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/documents?limit=25")
        response = await client.get(
            "/api/documents?limit=25",
            headers={"Authorization": "Bearer token"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert service.tenant_ids == [tenant_id]
    assert response.json() == [
        {
            "documentId": response.json()[0]["documentId"],
            "title": "Security policy",
            "accessMode": "tenant",
            "canManage": True,
            "versionId": str(service.version_id),
            "versionNumber": 2,
            "filename": "security-policy.pdf",
            "mediaType": "application/pdf",
            "sizeBytes": 524288,
            "versionStatus": "failed",
            "generationId": response.json()[0]["generationId"],
            "ingestionStatus": "failed",
            "ingestionStage": "embed",
            "errorCode": "embedding_provider_unavailable",
            "createdAt": "2026-08-22T00:00:00Z",
            "updatedAt": "2026-08-23T00:00:00Z",
        }
    ]


async def test_document_inventory_limit_is_validated_before_service_call() -> None:
    principal = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")
    service = StubDocumentInventoryService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        document_inventory_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/documents?limit=201",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 422
    assert service.tenant_ids == []
