from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import (
    Document,
    DocumentGrantResult,
    DocumentPolicyForbidden,
    DocumentPolicyNotFound,
)


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubDocumentPolicyService:
    def __init__(self, *, tenant_id: UUID, actor_id: UUID) -> None:
        self.document = Document(
            id=uuid4(),
            tenant_id=tenant_id,
            created_by=actor_id,
            title="Security policy",
            access_mode="tenant",
        )
        self.grant = DocumentGrantResult(
            grant_id=uuid4(),
            document_id=self.document.id,
            grantee_user_id=uuid4(),
            grantee_role=None,
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None

    def _record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.error is not None:
            raise self.error

    async def get_document(self, **kwargs: Any) -> Document:
        self._record("get", kwargs)
        return self.document

    async def set_access_mode(self, **kwargs: Any) -> Document:
        self._record("update", kwargs)
        self.document.access_mode = str(kwargs["access_mode"])
        return self.document

    async def list_grants(self, **kwargs: Any) -> tuple[DocumentGrantResult, ...]:
        self._record("list", kwargs)
        return (self.grant,)

    async def add_grant(self, **kwargs: Any) -> DocumentGrantResult:
        self._record("add", kwargs)
        return DocumentGrantResult(
            grant_id=self.grant.grant_id,
            document_id=self.document.id,
            grantee_user_id=kwargs.get("grantee_user_id"),
            grantee_role=kwargs.get("grantee_role"),
        )

    async def remove_grant(self, **kwargs: Any) -> None:
        self._record("remove", kwargs)


def _app(*, principal: PrincipalContext, service: StubDocumentPolicyService) -> Any:
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        document_policy_service=service,
    )


async def test_document_policy_endpoints_are_authenticated_scoped_and_typed() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        role="owner",
    )
    service = StubDocumentPolicyService(tenant_id=tenant_id, actor_id=actor_id)
    app = _app(principal=principal, service=service)
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get(f"/api/documents/{service.document.id}/access")
        current = await client.get(f"/api/documents/{service.document.id}/access", headers=headers)
        updated = await client.put(
            f"/api/documents/{service.document.id}/access",
            headers=headers,
            json={"accessMode": "restricted"},
        )
        listed = await client.get(f"/api/documents/{service.document.id}/grants", headers=headers)
        added = await client.post(
            f"/api/documents/{service.document.id}/grants",
            headers=headers,
            json={"granteeRole": "member"},
        )
        removed = await client.delete(
            f"/api/documents/{service.document.id}/grants/{service.grant.grant_id}",
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert current.status_code == 200
    assert current.json() == {
        "documentId": str(service.document.id),
        "accessMode": "tenant",
        "canManage": True,
    }
    assert updated.status_code == 200
    assert updated.json()["accessMode"] == "restricted"
    assert listed.status_code == 200
    assert listed.json()[0]["granteeUserId"] == str(service.grant.grantee_user_id)
    assert added.status_code == 201
    assert added.json()["granteeRole"] == "member"
    assert removed.status_code == 204
    assert [operation for operation, _ in service.calls] == [
        "get",
        "update",
        "list",
        "add",
        "remove",
    ]
    for _, kwargs in service.calls:
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["actor_id"] == actor_id


async def test_document_policy_errors_are_non_disclosing_and_stable() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    principal = PrincipalContext(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        role="member",
    )
    service = StubDocumentPolicyService(tenant_id=tenant_id, actor_id=actor_id)
    app = _app(principal=principal, service=service)
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        service.error = DocumentPolicyForbidden()
        forbidden = await client.put(
            f"/api/documents/{service.document.id}/access",
            headers=headers,
            json={"accessMode": "restricted"},
        )
        service.error = DocumentPolicyNotFound()
        missing = await client.get(f"/api/documents/{service.document.id}/access", headers=headers)
        service.error = None
        invalid = await client.post(
            f"/api/documents/{service.document.id}/grants",
            headers=headers,
            json={"granteeUserId": str(uuid4()), "granteeRole": "member"},
        )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "document_policy_forbidden"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_policy_not_found"
    assert invalid.status_code == 422
    assert all(call[0] != "add" for call in service.calls)
