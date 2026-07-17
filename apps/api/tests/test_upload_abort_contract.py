from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads import UploadAbortConflict, UploadSessionNotFound


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class UnusedUploadCreationService:
    async def create(self, **_: object) -> object:
        raise AssertionError("create must not be called")


class StubUploadSessionService:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.abort_requests: list[dict[str, object]] = []
        self.error: Exception | None = None
        self.replayed = False

    async def get(self, **_: object) -> object:
        raise AssertionError("get must not be called")

    async def presign_part(self, **_: object) -> object:
        raise AssertionError("presign must not be called")

    async def complete(self, **_: object) -> object:
        raise AssertionError("complete must not be called")

    async def abort(self, **kwargs: object) -> object:
        self.abort_requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            session_id=self.session_id,
            status="aborted",
            replayed=self.replayed,
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(
        tenant_id=str(uuid4()),
        actor_id=str(uuid4()),
        role="owner",
    )


def _app(service: StubUploadSessionService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=UnusedUploadCreationService(),
        upload_session_service=service,
    )


@pytest.mark.parametrize("replayed", [False, True])
async def test_abort_returns_empty_no_content_for_first_and_repeated_calls(
    replayed: bool,
) -> None:
    service = StubUploadSessionService()
    service.replayed = replayed
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            f"/api/upload-sessions/{service.session_id}",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 204
    assert response.content == b""
    assert len(service.abort_requests) == 1
    assert service.abort_requests[0]["session_id"] == service.session_id


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (UploadSessionNotFound(), 404, "upload_session_not_found"),
        (UploadAbortConflict(), 409, "upload_abort_conflict"),
        (ObjectStoreUnavailable(), 503, "object_store_unavailable"),
    ],
)
async def test_abort_errors_use_typed_envelopes(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    service = StubUploadSessionService()
    service.error = error
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            f"/api/upload-sessions/{service.session_id}",
            headers={
                "Authorization": "Bearer token",
                "X-Request-ID": "request-abort-error",
            },
        )

    assert response.status_code == expected_status
    assert response.json()["error"] == {
        "code": expected_code,
        "message": error.message,
        "requestId": "request-abort-error",
    }


async def test_abort_requires_bearer_authentication_before_service_call() -> None:
    service = StubUploadSessionService()
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(f"/api/upload-sessions/{service.session_id}")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "auth_missing"
    assert service.abort_requests == []


def test_openapi_declares_abort_route_and_typed_errors() -> None:
    app = _app(StubUploadSessionService())
    schema = app.openapi()
    operation = schema["paths"]["/api/upload-sessions/{session_id}"]["delete"]

    assert operation["security"] == [{"BearerAuth": []}]
    assert {"204", "401", "404", "409", "500", "502", "503"} <= set(operation["responses"])
    assert "content" not in operation["responses"]["204"]
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
