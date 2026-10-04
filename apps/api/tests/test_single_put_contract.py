from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.browser_auth.demo import allow_demo_operation
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store.models import PresignedObjectUpload
from enterprise_doc_core.uploads.service import CreateUploadSessionResult
from enterprise_doc_core.uploads.session_service import CompleteUploadSessionResult


class Resolver:
    async def resolve(self, token):
        return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


class Service:
    def __init__(self):
        self.calls = []
        self.id = uuid4()

    async def create(self, *, request, **kwargs):
        self.calls.append(("create", request))
        return CreateUploadSessionResult(
            self.id,
            "active",
            request.filename,
            "txt",
            request.media_type,
            request.size_bytes,
            request.sha256,
            1024 * 1024,
            1,
            datetime.now(UTC) + timedelta(minutes=5),
            False,
            request.transport.value,
        )

    async def presign_single_put(self, **kwargs):
        self.calls.append(("sign", kwargs))
        return PresignedObjectUpload(
            "https://objects.example.test/signed", {"If-None-Match": "*"}, 60
        )

    async def complete(self, *, request, **kwargs):
        self.calls.append(("complete", request))
        assert request.parts == ()
        return CompleteUploadSessionResult(
            self.id, "completed", uuid4(), uuid4(), datetime.now(UTC), False
        )


def app(service):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        upload_creation_service=service,
        upload_session_service=service,
    )


@pytest.mark.parametrize("route", ["presign", "complete"])
async def test_single_put_requires_auth_and_rejects_unexpected_body(route):
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app(service)), base_url="http://test"
    ) as client:
        path = f"/api/upload-sessions/{service.id}/object/{route}"
        response = await client.post(path, json={})
        assert response.status_code == 401
        response = await client.post(
            path, json={"objectKey": "foreign"}, headers={"Authorization": "Bearer test"}
        )
        assert response.status_code == 422
        assert not service.calls
        response = await client.post(path, json={}, headers={"Authorization": "Bearer test"})
        assert response.status_code == 200
        assert len(service.calls) == 1
        assert "objectKey" not in response.json()


@pytest.mark.parametrize("transport", ["multipart", "single_put"])
async def test_create_transport_is_explicit_and_legacy_response_stays_compatible(transport):
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app(service)), base_url="http://test"
    ) as client:
        body = {"filename": "a.txt", "sizeBytes": 25, "mediaType": "text/plain", "sha256": "a" * 64}
        if transport == "single_put":
            body["transport"] = transport
        response = await client.post(
            "/api/upload-sessions",
            json=body,
            headers={"Authorization": "Bearer test", "Idempotency-Key": "one"},
        )
        assert response.status_code == 201
        if transport == "single_put":
            assert response.json()["transport"] == transport
        else:
            assert "transport" not in response.json()
        assert service.calls[0][1].transport.value == transport


def test_demo_only_allows_the_two_explicit_single_put_mutations():
    base = f"/api/upload-sessions/{uuid4()}/object"
    allow_demo_operation("POST", base + "/presign")
    allow_demo_operation("POST", base + "/complete")
    for method, path in [
        ("GET", base + "/presign"),
        ("POST", base + "/delete"),
        ("DELETE", base),
        ("POST", base + "/complete/extra"),
    ]:
        with pytest.raises(ApiError):
            allow_demo_operation(method, path)
