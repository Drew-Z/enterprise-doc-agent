from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings


async def test_browser_session_reports_disabled_without_legacy_token_fallback() -> None:
    app = create_app(settings=ApiSettings(_env_file=None), checkers=[])
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://app.example.test"
        ) as client:
            response = await client.get("/auth/session")
    assert response.status_code == 200
    assert response.json() == {"status": "disabled"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
