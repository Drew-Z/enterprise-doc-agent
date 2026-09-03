from __future__ import annotations

import logging
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.middleware import ApiAuthenticationMiddleware, RequestContextMiddleware
from enterprise_doc_core.context import PrincipalContext


class StubResolver:
    async def resolve(self, _: str) -> PrincipalContext:
        return PrincipalContext(tenant_id="tenant-1", actor_id="actor-1", role="member")


def _app() -> FastAPI:
    app = FastAPI()
    app.state.principal_resolver = StubResolver()
    app.add_middleware(ApiAuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/protected")
    async def protected(
        _principal: Annotated[PrincipalContext, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {"status": "ok"}

    return app


async def test_auth_failure_emits_bounded_security_signal_without_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()
    caplog.set_level(logging.WARNING, logger="enterprise_doc_api.auth")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/protected")
        malformed = await client.get(
            "/api/protected",
            headers={"Authorization": "Basic secret-token-value"},
        )

    assert missing.status_code == 401
    assert malformed.status_code == 401
    auth_records = [record for record in caplog.records if record.name == "enterprise_doc_api.auth"]
    assert [record.message for record in auth_records] == ["auth_failed", "auth_failed"]
    assert [record.event_data["error_code"] for record in auth_records] == [
        "auth_missing",
        "auth_invalid",
    ]
    assert all(record.event_data["surface"] == "api" for record in auth_records)
    assert "secret-token-value" not in caplog.text


async def test_successful_bearer_request_does_not_emit_login_like_auth_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.state.principal_resolver = StubResolver()
    app.add_middleware(ApiAuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/protected")
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    caplog.set_level(logging.WARNING, logger="enterprise_doc_api.auth")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/protected",
            headers={"Authorization": "Bearer safe-token-value"},
        )

    assert response.status_code == 200
    assert not [record for record in caplog.records if record.name == "enterprise_doc_api.auth"]
