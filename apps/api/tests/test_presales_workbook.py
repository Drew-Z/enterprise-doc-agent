from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import register_error_handlers
from enterprise_doc_api.presales.router import router
from enterprise_doc_core.context import PrincipalContext


@pytest.mark.asyncio
async def test_workbook_request_is_bounded_and_errors_do_not_echo_content():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    principal = PrincipalContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        actor_id="00000000-0000-0000-0000-000000000002",
        role="owner",
    )
    app.dependency_overrides[get_current_principal] = lambda: principal
    service = AsyncMock()
    app.state.presales_service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/presales/workbooks/preview",
            json={
                "filename": "client.xlsx",
                "contentBase64": "secret",
                "unknown": "customer secret",
            },
        )
        assert response.status_code == 422
        assert "secret" not in response.text
        response = await client.post(
            "/api/presales/workbooks/preview",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "9999999999999999999999999",
            },
        )
        assert response.status_code == 413
        service.preview_workbook.assert_not_awaited()
