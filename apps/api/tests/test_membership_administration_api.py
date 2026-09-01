from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity.membership_service import (
    MembershipAdministrationResult,
    MembershipLastOwnerRequired,
    MembershipSelfMutationForbidden,
)


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubMembershipAdministrationService:
    def __init__(self, *, tenant_id: UUID, user_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.membership_id = uuid4()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.mutation_error: Exception | None = None

    async def list_members(self, **kwargs: object) -> tuple[MembershipAdministrationResult, ...]:
        self.calls.append(("list", kwargs))
        return (self._result(role="owner", is_active=True),)

    async def provision_member(self, **kwargs: object) -> MembershipAdministrationResult:
        self.calls.append(("provision", kwargs))
        return self._result(role=str(kwargs["member_role"]), is_active=True)

    async def change_role(self, **kwargs: object) -> MembershipAdministrationResult:
        self.calls.append(("role", kwargs))
        if self.mutation_error is not None:
            raise self.mutation_error
        return self._result(role=str(kwargs["member_role"]), is_active=True)

    async def deactivate_member(self, **kwargs: object) -> MembershipAdministrationResult:
        self.calls.append(("deactivate", kwargs))
        if self.mutation_error is not None:
            raise self.mutation_error
        return self._result(role="member", is_active=False)

    async def activate_member(self, **kwargs: object) -> MembershipAdministrationResult:
        self.calls.append(("activate", kwargs))
        return self._result(role="member", is_active=True)

    def _result(self, *, role: str, is_active: bool) -> MembershipAdministrationResult:
        now = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
        return MembershipAdministrationResult(
            membership_id=self.membership_id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            email="member@example.test",
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )


async def test_owner_manages_tenant_membership_lifecycle() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    user_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        role="owner",
    )
    service = StubMembershipAdministrationService(tenant_id=tenant_id, user_id=user_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        membership_administration_service=service,
    )
    headers = {
        "Authorization": "Bearer token",
        "X-Request-ID": "req-member-1",
        "X-Correlation-ID": "corr-member-1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/members?q=member", headers=headers)
        provisioned = await client.post(
            "/api/members",
            headers=headers,
            json={"email": "member@example.test", "role": "member"},
        )
        role_changed = await client.put(
            f"/api/members/{service.membership_id}/role",
            headers=headers,
            json={"role": "owner"},
        )
        deactivated = await client.delete(
            f"/api/members/{service.membership_id}",
            headers=headers,
        )
        activated = await client.post(
            f"/api/members/{service.membership_id}/activate",
            headers=headers,
        )

    assert listed.status_code == 200
    assert listed.json()[0]["email"] == "member@example.test"
    assert provisioned.status_code == 200
    assert role_changed.json()["role"] == "owner"
    assert deactivated.json()["isActive"] is False
    assert activated.json()["isActive"] is True

    calls = {name: kwargs for name, kwargs in service.calls}
    assert calls["list"] == {
        "tenant_id": tenant_id,
        "role": "owner",
        "query": "member",
        "limit": 100,
    }
    assert calls["provision"]["tenant_id"] == tenant_id
    assert calls["provision"]["actor_id"] == actor_id
    assert calls["provision"]["request_id"] == "req-member-1"
    assert calls["provision"]["correlation_id"] == "corr-member-1"
    assert calls["role"]["member_role"] == "owner"
    assert calls["deactivate"]["membership_id"] == service.membership_id
    assert calls["activate"]["membership_id"] == service.membership_id


async def test_membership_administration_is_owner_only() -> None:
    tenant_id = uuid4()
    service = StubMembershipAdministrationService(tenant_id=tenant_id, user_id=uuid4())
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(
            PrincipalContext(
                tenant_id=str(tenant_id),
                actor_id=str(uuid4()),
                role="member",
            )
        ),
        membership_administration_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/members",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "membership_administration_forbidden"
    assert service.calls == []


async def test_membership_safety_errors_have_stable_contracts() -> None:
    tenant_id = uuid4()
    service = StubMembershipAdministrationService(tenant_id=tenant_id, user_id=uuid4())
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(
            PrincipalContext(
                tenant_id=str(tenant_id),
                actor_id=str(uuid4()),
                role="owner",
            )
        ),
        membership_administration_service=service,
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        service.mutation_error = MembershipLastOwnerRequired()
        last_owner = await client.delete(
            f"/api/members/{service.membership_id}",
            headers=headers,
        )
        service.mutation_error = MembershipSelfMutationForbidden()
        self_mutation = await client.put(
            f"/api/members/{service.membership_id}/role",
            headers=headers,
            json={"role": "member"},
        )

    assert last_owner.status_code == 409
    assert last_owner.json()["error"]["code"] == "membership_last_owner_required"
    assert self_mutation.status_code == 409
    assert self_mutation.json()["error"]["code"] == "membership_self_mutation_forbidden"
