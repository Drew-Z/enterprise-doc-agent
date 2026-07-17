from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads import (
    UploadCompletionPartsInvalid,
    UploadCompletionVerificationFailed,
    UploadSessionExpired,
    UploadSessionNotFound,
)

CHECKSUM = base64.b64encode(b"a" * 32).decode("ascii")


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
        self.document_id = uuid4()
        self.version_id = uuid4()
        self.completed_at = datetime.now(UTC)
        self.complete_requests: list[dict[str, object]] = []
        self.error: Exception | None = None
        self.replayed = False

    async def get(self, **_: object) -> object:
        raise AssertionError("get must not be called")

    async def presign_part(self, **_: object) -> object:
        raise AssertionError("presign must not be called")

    async def complete(self, **kwargs: object) -> object:
        self.complete_requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            session_id=self.session_id,
            status="completed",
            document_id=self.document_id,
            version_id=self.version_id,
            completed_at=self.completed_at,
            replayed=self.replayed,
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(
        tenant_id=str(uuid4()),
        actor_id=str(uuid4()),
        role="owner",
    )


def _payload() -> dict[str, object]:
    return {
        "parts": [
            {
                "partNumber": 1,
                "sizeBytes": 5,
                "etag": '"etag-one"',
                "checksumSha256": CHECKSUM,
            },
            {
                "partNumber": 2,
                "sizeBytes": 3,
                "etag": '"etag-two"',
                "checksumSha256": CHECKSUM,
            },
        ]
    }


def _app(service: StubUploadSessionService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=UnusedUploadCreationService(),
        upload_session_service=service,
    )


async def test_complete_returns_durable_version_without_store_identifiers() -> None:
    service = StubUploadSessionService()
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers={"Authorization": "Bearer token"},
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "sessionId": str(service.session_id),
        "status": "completed",
        "documentId": str(service.document_id),
        "versionId": str(service.version_id),
        "completedAt": response.json()["completedAt"],
        "replayed": False,
    }
    assert "objectKey" not in response.json()
    assert "objectStoreUploadId" not in response.json()
    assert "etag" not in response.json()
    assert len(service.complete_requests) == 1
    request = service.complete_requests[0]["request"]
    assert [part.part_number for part in request.parts] == [1, 2]
    assert [part.size_bytes for part in request.parts] == [5, 3]


async def test_complete_replay_uses_the_same_response_contract() -> None:
    service = StubUploadSessionService()
    service.replayed = True
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers={"Authorization": "Bearer token"},
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json()["versionId"] == str(service.version_id)
    assert response.json()["replayed"] is True


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (UploadSessionNotFound(), 404, "upload_session_not_found"),
        (UploadSessionExpired(), 410, "upload_session_expired"),
        (UploadCompletionPartsInvalid(), 409, "upload_completion_parts_invalid"),
        (
            UploadCompletionVerificationFailed(),
            409,
            "upload_completion_verification_failed",
        ),
        (ObjectStoreUnavailable(), 503, "object_store_unavailable"),
    ],
)
async def test_complete_errors_use_typed_envelopes(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    service = StubUploadSessionService()
    service.error = error
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers={
                "Authorization": "Bearer token",
                "X-Request-ID": "request-complete-error",
            },
            json=_payload(),
        )

    assert response.status_code == expected_status
    assert response.json()["error"] == {
        "code": expected_code,
        "message": error.message,
        "requestId": "request-complete-error",
    }


@pytest.mark.parametrize("invalid_value", [True, 1.0, "1"])
async def test_complete_requires_strict_part_numbers_and_sizes(invalid_value: object) -> None:
    service = StubUploadSessionService()
    app = _app(service)
    payload = _payload()
    payload["parts"][0]["partNumber"] = invalid_value
    payload["parts"][0]["sizeBytes"] = invalid_value

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers={"Authorization": "Bearer token"},
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert service.complete_requests == []


@pytest.mark.parametrize("location", ["request", "part"])
async def test_complete_rejects_unknown_request_fields(location: str) -> None:
    service = StubUploadSessionService()
    app = _app(service)
    payload = _payload()
    if location == "request":
        payload["unexpectedField"] = True
    else:
        payload["parts"][0]["checksumSha265"] = CHECKSUM

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers={"Authorization": "Bearer token"},
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert service.complete_requests == []


@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        ({}, "auth_missing"),
        ({"Authorization": "Basic token"}, "auth_invalid"),
    ],
)
async def test_complete_requires_one_valid_bearer_header(
    headers: dict[str, str],
    expected_code: str,
) -> None:
    service = StubUploadSessionService()
    app = _app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/upload-sessions/{service.session_id}/complete",
            headers=headers,
            json=_payload(),
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == expected_code
    assert service.complete_requests == []


def test_openapi_declares_complete_route_and_typed_errors() -> None:
    app = _app(StubUploadSessionService())
    schema = app.openapi()
    operation = schema["paths"]["/api/upload-sessions/{session_id}/complete"]["post"]

    assert operation["security"] == [{"BearerAuth": []}]
    assert {"200", "401", "404", "409", "410", "422", "500", "502", "503"} <= set(
        operation["responses"]
    )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UploadSessionCompleteResponse"
    }
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
