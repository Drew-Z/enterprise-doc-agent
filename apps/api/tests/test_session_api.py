from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth import JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.auth import LocalTokenRevocationResult
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubTokenRevocationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def revoke(self, **kwargs: object) -> LocalTokenRevocationResult:
        self.calls.append(kwargs)
        now = datetime.now(UTC)
        return LocalTokenRevocationResult(
            revocation_id=uuid4(),
            tenant_id=cast(UUID, kwargs["tenant_id"]),
            actor_id=cast(UUID, kwargs["actor_id"]),
            token_id=str(kwargs["token_id"]),
            issued_at=cast(datetime, kwargs["issued_at"]),
            expires_at=cast(datetime, kwargs["expires_at"]),
            revoked_at=now,
            reason="logout",
            already_revoked=False,
        )


async def test_session_api_requires_authentication_and_returns_owner_capabilities() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(
            PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/session")
        response = await client.get("/api/session", headers={"Authorization": "Bearer token"})

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "tenantId": str(tenant_id),
        "actorId": str(actor_id),
        "role": "owner",
        "capabilities": {
            "documentRead": True,
            "documentWrite": True,
            "agentRunCreate": True,
            "auditRead": True,
            "auditExport": True,
            "approvalDecide": True,
        },
    }


async def test_session_api_returns_restricted_member_capabilities() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(
            PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="member")
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/session", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json()["capabilities"] == {
        "documentRead": True,
        "documentWrite": True,
        "agentRunCreate": True,
        "auditRead": True,
        "auditExport": False,
        "approvalDecide": False,
    }


async def test_session_logout_revokes_the_current_local_jwt() -> None:
    settings = ApiSettings(_env_file=None)
    tenant_id = uuid4()
    actor_id = uuid4()
    token = JwtTokenCodec(settings.auth).issue_local_token(
        tenant_id=tenant_id,
        actor_id=actor_id,
        now=datetime.now(UTC),
    )
    service = StubTokenRevocationService()
    app = create_app(
        settings=settings,
        checkers=[],
        principal_resolver=StubPrincipalResolver(
            PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
        ),
        token_revocation_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/session/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["revoked"] is True
    assert response.json()["alreadyRevoked"] is False
    assert service.calls[0]["tenant_id"] == tenant_id
    assert service.calls[0]["actor_id"] == actor_id
    assert service.calls[0]["token_id"]


async def test_session_logout_does_not_claim_to_revoke_external_oidc_tokens() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    )
    principal = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")
    app = create_app(
        settings=settings,
        checkers=[],
        external_principal_resolver=StubPrincipalResolver(principal),
        token_revocation_service=StubTokenRevocationService(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/session/logout",
            headers={"Authorization": "Bearer external-oidc-token"},
        )

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "session_logout_external_unsupported"
