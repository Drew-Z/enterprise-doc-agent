from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.identity import ScimUserPage, ScimUserResult


class StubPrincipalResolver:
    async def resolve(self, _: str) -> PrincipalContext:
        return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


class StubScimService:
    def __init__(self, *, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.calls: list[dict[str, object]] = []
        self.user_id = uuid4()
        self.membership_id = uuid4()
        self.binding_id = uuid4()

    async def sync_user(self, **kwargs: object) -> ScimUserResult:
        self.calls.append(kwargs)
        return self._result(
            subject=str(kwargs["subject"]),
            email=str(kwargs.get("email") or "member@example.test"),
            role=str(kwargs.get("role") or "member"),
            is_active=bool(kwargs["is_active"]),
        )

    async def get_user(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        subject: str,
    ) -> ScimUserResult:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "issuer": issuer,
                "subject": subject,
                "operation": "get",
            }
        )
        return self._result(
            subject=subject,
            email="member@example.test",
            role="member",
            is_active=True,
        )

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        start_index: int = 1,
        count: int = 100,
        user_name: str | None = None,
        external_id: str | None = None,
    ) -> ScimUserPage:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "issuer": issuer,
                "start_index": start_index,
                "count": count,
                "user_name": user_name,
                "external_id": external_id,
                "operation": "list",
            }
        )
        resources = (
            (
                self._result(
                    subject="subject-123",
                    email="member@example.test",
                    role="member",
                    is_active=True,
                ),
            )
            if external_id in {None, "subject-123"}
            else ()
        )
        return ScimUserPage(
            total_results=len(resources),
            start_index=start_index,
            items_per_page=len(resources),
            resources=resources,
        )

    def _result(
        self,
        *,
        subject: str,
        email: str,
        role: str,
        is_active: bool,
    ) -> ScimUserResult:
        return ScimUserResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            membership_id=self.membership_id,
            binding_id=self.binding_id,
            subject=subject,
            email=email,
            role=role,
            is_active=is_active,
        )


def _settings(tenant_id: UUID, *, enabled: bool = True) -> ApiSettings:
    return ApiSettings(
        _env_file=None,
        auth={
            "scim_enabled": enabled,
            "scim_issuer": "https://idp.example.test/scim",
            "scim_tenant_tokens": {str(tenant_id): "s" * 32},
        },
    )


async def test_scim_upsert_requires_tenant_token_and_maps_groups(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="enterprise_doc_api.auth")
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )
    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "Member@Example.Test",
        "externalId": "subject-123",
        "active": True,
        "groups": [{"value": "tenant-member"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.put(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            json=payload,
        )
        valid = await client.put(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + "s" * 32},
            json=payload,
        )
        read = await client.get(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + "s" * 32},
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "scim_auth_invalid"
    assert "s" * 32 not in caplog.text
    assert any(
        record.getMessage() == "auth_failed"
        and record.__dict__.get("event_data", {}).get("surface") == "scim"
        for record in caplog.records
    )
    assert valid.status_code == 200
    assert valid.json()["userName"] == "Member@Example.Test"
    assert valid.json()["active"] is True
    assert service.calls[0]["role"] == "member"
    assert service.calls[0]["issuer"] == "https://idp.example.test/scim"
    assert read.status_code == 200
    assert read.json()["active"] is True
    assert read.json()["groups"] == []
    assert service.calls[-1]["operation"] == "get"


async def test_scim_delete_is_idempotent_and_tenant_scoped() -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=ApiSettings(
            _env_file=None,
            auth={
                "scim_enabled": True,
                "scim_issuer": "https://idp.example.test/scim",
                "scim_tenant_tokens": {
                    str(tenant_id): "s" * 32,
                    str(other_tenant_id): "o" * 32,
                },
            },
        ),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.delete(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + "s" * 32},
        )
        wrong_token = await client.delete(
            f"/scim/v2/tenants/{other_tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + "s" * 32},
        )

    assert deleted.status_code == 204
    assert wrong_token.status_code == 401
    assert service.calls[0]["email"] is None
    assert service.calls[0]["is_active"] is False


async def test_scim_is_disabled_by_default() -> None:
    tenant_id = uuid4()
    app = create_app(settings=ApiSettings(_env_file=None), checkers=[])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            json={"userName": "member@example.test", "groups": [{"value": "member"}]},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scim_disabled"


async def test_scim_discovery_exposes_the_supported_constrained_contract() -> None:
    tenant_id = uuid4()
    app = create_app(settings=_settings(tenant_id), checkers=[])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider = await client.get("/scim/v2/ServiceProviderConfig")
        resource_types = await client.get("/scim/v2/ResourceTypes")
        schemas = await client.get("/scim/v2/Schemas")

    assert provider.status_code == 200
    assert provider.json()["filter"] == {"supported": True, "maxResults": 200}
    assert provider.json()["patch"]["supported"] is True
    assert provider.json()["bulk"] == {
        "supported": True,
        "maxOperations": 50,
        "maxPayloadSize": 1_048_576,
    }
    assert resource_types.status_code == 200
    assert resource_types.json()["resources"][0]["endpoint"] == "/Users"
    assert schemas.status_code == 200
    assert schemas.json()["resources"][0]["id"] == "urn:ietf:params:scim:schemas:core:2.0:User"


async def test_scim_users_collection_supports_bounded_pagination_and_equality_filters() -> None:
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/scim/v2/tenants/{tenant_id}/Users",
            headers={"Authorization": "Bearer " + "s" * 32},
            params={
                "startIndex": 2,
                "count": 1,
                "filter": 'userName eq "member@example.test"',
            },
        )
        unsupported = await client.get(
            f"/scim/v2/tenants/{tenant_id}/Users",
            headers={"Authorization": "Bearer " + "s" * 32},
            params={"filter": 'userName co "member"'},
        )
        combined = await client.get(
            f"/scim/v2/tenants/{tenant_id}/Users",
            headers={"Authorization": "Bearer " + "s" * 32},
            params={
                "filter": 'userName eq "member@example.test" and externalId eq "subject-123"',
            },
        )

    assert response.status_code == 200
    assert response.json()["startIndex"] == 2
    assert response.json()["itemsPerPage"] == 1
    assert response.json()["resources"][0]["id"] == "subject-123"
    assert service.calls[-1]["user_name"] == "member@example.test"
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "scim_filter_unsupported"
    assert combined.status_code == 200
    assert combined.json()["itemsPerPage"] == 1
    assert service.calls[-1]["user_name"] == "member@example.test"
    assert service.calls[-1]["external_id"] == "subject-123"


async def test_scim_bulk_processes_bounded_user_operations_and_reports_each_status() -> None:
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )
    token = "s" * 32
    payload = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"],
        "operations": [
            {
                "method": "POST",
                "path": "/Users",
                "bulkId": "create-1",
                "data": {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                    "userName": "new@example.test",
                    "externalId": "new-subject",
                    "active": True,
                    "groups": [{"value": "tenant-member"}],
                },
            },
            {
                "method": "DELETE",
                "path": "/Users/subject-123",
                "bulkId": "delete-1",
            },
            {
                "method": "PATCH",
                "path": "/Users/subject-123",
                "bulkId": "unsupported-1",
                "data": {"operations": [{"op": "replace", "path": "active", "value": False}]},
            },
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/scim/v2/tenants/{tenant_id}/Bulk",
            headers={"Authorization": "Bearer " + token},
            json=payload,
        )
        too_many = await client.post(
            f"/scim/v2/tenants/{tenant_id}/Bulk",
            headers={"Authorization": "Bearer " + token},
            json={"operations": payload["operations"] * 17},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:BulkResponse"]
    assert [operation["status"] for operation in body["operations"]] == ["200", "204", "200"]
    assert body["operations"][0]["bulkId"] == "create-1"
    assert body["operations"][0]["response"]["externalId"] == "new-subject"
    assert body["operations"][1]["response"] is None
    assert body["operations"][2]["response"]["active"] is False
    assert len(service.calls) == 4
    assert [call["subject"] for call in service.calls if call.get("operation") != "get"] == [
        "new-subject",
        "subject-123",
        "subject-123",
    ]
    assert too_many.status_code == 422


async def test_scim_bulk_patch_preserves_errors_and_rejects_unsupported_paths() -> None:
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )
    token = "s" * 32
    payload = {
        "operations": [
            {
                "method": "PATCH",
                "path": "/Users/subject-123",
                "bulkId": "invalid-patch",
                "data": {"operations": [{"op": "replace", "path": "groups", "value": []}]},
            },
            {
                "method": "PATCH",
                "path": "/Users",
                "bulkId": "missing-subject",
                "data": {"operations": [{"op": "replace", "path": "active", "value": False}]},
            },
        ]
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/scim/v2/tenants/{tenant_id}/Bulk",
            headers={"Authorization": "Bearer " + token},
            json=payload,
        )

    assert response.status_code == 200
    operations = response.json()["operations"]
    assert [operation["status"] for operation in operations] == ["400", "400"]
    assert operations[0]["response"]["scimType"] == "scim_patch_path_unsupported"
    assert operations[1]["response"]["scimType"] == "scim_bulk_subject_required"


async def test_scim_patch_allows_bounded_active_and_username_replacements() -> None:
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )
    token = "s" * 32

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deactivated = await client.patch(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + token},
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "operations": [{"op": "replace", "path": "active", "value": False}],
            },
        )
        renamed = await client.patch(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + token},
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "operations": [{"op": "replace", "path": "userName", "value": "new@example.test"}],
            },
        )
        unsupported = await client.patch(
            f"/scim/v2/tenants/{tenant_id}/Users/subject-123",
            headers={"Authorization": "Bearer " + token},
            json={
                "operations": [{"op": "add", "path": "groups", "value": []}],
            },
        )

    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    deactivation_call = next(call for call in service.calls if call.get("is_active") is False)
    assert deactivation_call["is_active"] is False
    assert renamed.status_code == 200
    assert renamed.json()["userName"] == "new@example.test"
    rename_call = next(call for call in service.calls if call.get("email") == "new@example.test")
    assert rename_call["is_active"] is True
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "scim_patch_operation_unsupported"


async def test_scim_patch_rejects_invalid_values_paths_and_inactive_username_changes() -> None:
    tenant_id = uuid4()
    service = StubScimService(tenant_id=tenant_id)
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=service,
    )
    token = "s" * 32
    endpoint = f"/scim/v2/tenants/{tenant_id}/Users/subject-123"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid_value = await client.patch(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={"operations": [{"op": "replace", "path": "active", "value": "false"}]},
        )
        unsupported_path = await client.patch(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={"operations": [{"op": "replace", "path": "groups", "value": []}]},
        )
        inactive_conflict = await client.patch(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={
                "operations": [
                    {"op": "replace", "path": "active", "value": False},
                    {"op": "replace", "path": "userName", "value": "new@example.test"},
                ]
            },
        )

    assert invalid_value.status_code == 400
    assert invalid_value.json()["error"]["code"] == "scim_patch_value_invalid"
    assert unsupported_path.status_code == 400
    assert unsupported_path.json()["error"]["code"] == "scim_patch_path_unsupported"
    assert inactive_conflict.status_code == 409
    assert inactive_conflict.json()["error"]["code"] == "scim_patch_inactive_conflict"


async def test_scim_patch_rejects_missing_users_and_more_than_eight_operations() -> None:
    tenant_id = uuid4()
    token = "s" * 32
    endpoint = f"/scim/v2/tenants/{tenant_id}/Users/subject-123"
    missing_service = StubScimService(tenant_id=tenant_id)

    async def missing_get_user(*, tenant_id: UUID, issuer: str, subject: str) -> None:
        return None

    missing_service.get_user = missing_get_user  # type: ignore[method-assign]
    app = create_app(
        settings=_settings(tenant_id),
        checkers=[],
        principal_resolver=StubPrincipalResolver(),
        scim_provisioning_service=missing_service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.patch(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={"operations": [{"op": "replace", "path": "active", "value": False}]},
        )
        too_many = await client.patch(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={"operations": [{"op": "replace", "path": "active", "value": True}] * 9},
        )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "scim_provisioning_not_found"
    assert too_many.status_code == 422
