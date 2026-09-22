from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import GenerationInput, PacketView, RequirementInput


async def test_presales_usage_unavailable_has_a_retryable_http_error() -> None:
    principal = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")

    class Resolver:
        async def resolve(self, _token: str) -> PrincipalContext:
            return principal

    class UnavailablePresales:
        async def generate(
            self,
            principal: PrincipalContext,
            packet_id: UUID,
            row_id: UUID,
            key: str,
        ) -> PacketView:
            raise PresalesError("presales_usage_unavailable")

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=UnavailablePresales(),  # type: ignore[arg-type]
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(
            f"/api/presales/{uuid4()}/rows/{uuid4()}/generate",
            headers={"Authorization": "Bearer owner", "Idempotency-Key": "generate"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "presales_usage_unavailable"
    assert response.json()["error"]["requestId"]
    assert response.headers["cache-control"] == "no-store"


async def test_api_factory_uses_explicit_presales_route_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRESALES__MODEL_ROUTE", "fallback")
    monkeypatch.setenv("PRESALES__MODEL_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("PRESALES__ROW_TIMEOUT_SECONDS", "150")
    settings = ApiSettings(
        _env_file=None,
        model={
            "fallback_provider": "openai_compatible",
            "fallback_base_url": "https://fallback.example/v1",
            "fallback_api_key": "test-secret",
            "fallback_model_name": "chosen-model",
        },
    )
    app = create_app(settings=settings, checkers=[])
    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429)

    async with app.router.lifespan_context(app):
        gateway = app.state.presales_service.generation.gateway
        gateway.transport = httpx.MockTransport(respond)
        with pytest.raises(PresalesError, match="presales_model_rate_limited"):
            await gateway.generate(
                GenerationInput(
                    requirement=RequirementInput(key="R1", text="Requirement"),
                    sources=[],
                    evidence=[],
                )
            )
    assert len(calls) == 1
    assert str(calls[0].url) == "https://fallback.example/v1/chat/completions"
    assert calls[0].extensions["timeout"]["read"] == 120
    assert gateway.model_name == "chosen-model"
