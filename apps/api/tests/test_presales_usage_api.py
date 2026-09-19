from __future__ import annotations

from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import PacketView


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
