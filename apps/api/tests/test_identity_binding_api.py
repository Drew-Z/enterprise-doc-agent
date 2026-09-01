from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity.service import (
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingResult,
    ExternalIdentityBindingTargetNotFound,
    ExternalIdentityMemberResult,
)


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubIdentityBindingService:
    def __init__(self, *, tenant_id: UUID, user_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.binding_id = uuid4()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.create_error: Exception | None = None

    async def list_bindings(self, **kwargs: object) -> tuple[ExternalIdentityBindingResult, ...]:
        self.calls.append(("list", kwargs))
        return (self._result(is_active=True),)

    async def list_active_members(
        self, **kwargs: object
    ) -> tuple[ExternalIdentityMemberResult, ...]:
        self.calls.append(("members", kwargs))
        return (
            ExternalIdentityMemberResult(
                user_id=self.user_id,
                email="owner@example.test",
                role="owner",
            ),
        )

    async def create_binding(self, **kwargs: object) -> ExternalIdentityBindingResult:
        self.calls.append(("create", kwargs))
        if self.create_error is not None:
            raise self.create_error
        return self._result(is_active=True)

    async def activate_binding(self, **kwargs: object) -> ExternalIdentityBindingResult:
        self.calls.append(("activate", kwargs))
        return self._result(is_active=True)

    async def deactivate_binding(self, **kwargs: object) -> ExternalIdentityBindingResult:
        self.calls.append(("deactivate", kwargs))
        return self._result(is_active=False)

    def _result(self, *, is_active: bool) -> ExternalIdentityBindingResult:
        now = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
        return ExternalIdentityBindingResult(
            binding_id=self.binding_id,
            tenant_id=self.tenant_id,
            issuer="https://idp.example.test",
            subject="subject-123",
            user_id=self.user_id,
            user_email="owner@example.test",
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )


async def test_owner_manages_tenant_scoped_external_identity_bindings() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    user_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        role="owner",
    )
    service = StubIdentityBindingService(tenant_id=tenant_id, user_id=user_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        external_identity_binding_service=service,
    )
    headers = {
        "Authorization": "Bearer token",
        "X-Request-ID": "req-binding-1",
        "X-Correlation-ID": "corr-binding-1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        members = await client.get(
            "/api/identity-bindings/members?q=owner%40example.test",
            headers=headers,
        )
        listed = await client.get("/api/identity-bindings", headers=headers)
        created = await client.post(
            "/api/identity-bindings",
            headers=headers,
            json={
                "issuer": "https://idp.example.test",
                "subject": "subject-123",
                "userId": str(user_id),
            },
        )
        deactivated = await client.delete(
            f"/api/identity-bindings/{service.binding_id}",
            headers=headers,
        )
        activated = await client.post(
            f"/api/identity-bindings/{service.binding_id}/activate",
            headers=headers,
        )

    assert members.status_code == 200
    assert members.json() == [
        {"userId": str(user_id), "email": "owner@example.test", "role": "owner"}
    ]
    assert listed.status_code == 200
    assert listed.json()[0]["tenantId"] == str(tenant_id)
    assert created.status_code == 201
    assert created.json()["userEmail"] == "owner@example.test"
    assert deactivated.status_code == 200
    assert deactivated.json()["isActive"] is False
    assert activated.status_code == 200
    assert activated.json()["isActive"] is True
    calls = {name: kwargs for name, kwargs in service.calls}
    assert calls["members"] == {
        "tenant_id": tenant_id,
        "role": "owner",
        "query": "owner@example.test",
        "limit": 50,
    }
    assert calls["list"] == {"tenant_id": tenant_id, "role": "owner"}
    assert calls["create"]["tenant_id"] == tenant_id
    assert calls["create"]["actor_id"] == actor_id
    assert calls["create"]["request_id"] == "req-binding-1"
    assert calls["create"]["correlation_id"] == "corr-binding-1"
    assert calls["deactivate"]["tenant_id"] == tenant_id
    assert calls["deactivate"]["binding_id"] == service.binding_id
    assert calls["activate"]["tenant_id"] == tenant_id
    assert calls["activate"]["binding_id"] == service.binding_id


async def test_external_identity_binding_management_is_owner_only() -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(uuid4()),
        role="member",
    )
    service = StubIdentityBindingService(tenant_id=tenant_id, user_id=user_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        external_identity_binding_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/identity-bindings",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "external_identity_binding_forbidden"
    assert service.calls == []


async def test_external_identity_binding_errors_have_stable_api_contracts() -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(uuid4()),
        role="owner",
    )
    service = StubIdentityBindingService(tenant_id=tenant_id, user_id=user_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        external_identity_binding_service=service,
    )
    headers = {"Authorization": "Bearer token"}
    payload = {
        "issuer": "https://idp.example.test",
        "subject": "subject-123",
        "userId": str(user_id),
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        service.create_error = ExternalIdentityBindingTargetNotFound()
        missing_target = await client.post("/api/identity-bindings", headers=headers, json=payload)
        service.create_error = ExternalIdentityBindingConflict()
        conflict = await client.post("/api/identity-bindings", headers=headers, json=payload)

    assert missing_target.status_code == 404
    assert missing_target.json()["error"]["code"] == "external_identity_binding_target_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "external_identity_binding_conflict"
