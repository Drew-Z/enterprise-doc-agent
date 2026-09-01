from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_worker.app import create_probe_app


@pytest.mark.integration
@pytest.mark.parametrize("app_factory", [create_app, create_probe_app])
async def test_runtime_readiness_uses_real_local_dependencies(
    app_factory: Callable[[], FastAPI],
) -> None:
    app = app_factory()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "database": {"status": "up"},
        "redis": {"status": "up"},
        "object_store": {"status": "up"},
    }
    assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None
