from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import DocumentInventoryItemResult


class DependencyPrincipalResolver:
    def __init__(self, error: Exception | None) -> None:
        self.error = error

    async def resolve(self, _: str) -> PrincipalContext:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


class DependencyInventory:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls = 0

    async def list_versions(
        self, *, tenant_id: UUID, actor_id: UUID, role: str, limit: int = 100
    ) -> tuple[DocumentInventoryItemResult, ...]:
        self.calls += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return ()


@pytest.mark.parametrize("during_authentication", [True, False])
@pytest.mark.parametrize(
    ("error_type", "status", "code"),
    [
        (PoolTimeoutError, 503, "service_busy"),
        (TimeoutError, 500, "internal_error"),
        (RuntimeError, 500, "internal_error"),
    ],
)
async def test_dependency_pool_exhaustion_is_recoverable_without_leaking_details(
    during_authentication: bool,
    error_type: type[Exception],
    status: int,
    code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = error_type("private-database-password-and-connection-detail")
    resolver = DependencyPrincipalResolver(error if during_authentication else None)
    inventory = DependencyInventory(None if during_authentication else error)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=resolver,
        document_inventory_service=inventory,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/documents", headers={"Authorization": "Bearer fixture-credential"}
        )
        assert inventory.calls == (0 if during_authentication else 1)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        if status == 503 or during_authentication:
            assert response.json()["error"]["requestId"]
        assert response.headers.get("retry-after") == ("1" if status == 503 else None)
        assert "private-database" not in response.text + caplog.text
        assert "fixture-credential" not in response.text + caplog.text

        recovered = await client.get(
            "/api/documents", headers={"Authorization": "Bearer fixture-credential"}
        )
        assert recovered.status_code == 200
        assert recovered.json() == []
