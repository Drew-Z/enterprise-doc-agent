from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads import (
    UploadPartChecksumConflict,
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
        self.expires_at = datetime.now(UTC) + timedelta(hours=1)
        self.presign_requests: list[dict[str, object]] = []
        self.error: Exception | None = None

    async def get(self, **_: object) -> object:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            session_id=self.session_id,
            status="active",
            filename="contract.pdf",
            extension=".pdf",
            media_type="application/pdf",
            size_bytes=10,
            declared_sha256="a" * 64,
            part_size_bytes=5,
            expected_part_count=2,
            expires_at=self.expires_at,
            uploaded_parts=(
                SimpleNamespace(
                    part_number=1,
                    size_bytes=5,
                    etag='"etag-one"',
                    checksum_sha256_b64=CHECKSUM,
                ),
            ),
        )

    async def presign_part(self, **kwargs: object) -> object:
        self.presign_requests.append(kwargs)
        if self.error is not None:
            raise self.error
        request = kwargs["request"]
        return SimpleNamespace(
            part_number=kwargs["part_number"],
            size_bytes=request.size_bytes,
            checksum_sha256_b64=request.checksum_sha256_b64,
            url="http://store.test/presigned-part",
            headers={"x-amz-checksum-sha256": request.checksum_sha256_b64},
            expires_in_seconds=900,
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(
        tenant_id=str(uuid4()),
        actor_id=str(uuid4()),
        role="owner",
    )


async def test_get_and_presign_return_resume_contract_without_store_identifiers() -> None:
    service = StubUploadSessionService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=UnusedUploadCreationService(),
        upload_session_service=service,
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_response = await client.get(
            f"/api/upload-sessions/{service.session_id}",
            headers=headers,
        )
        presign_response = await client.post(
            f"/api/upload-sessions/{service.session_id}/parts/2/presign",
            headers=headers,
            json={"sizeBytes": 5, "checksumSha256": CHECKSUM},
        )

    assert get_response.status_code == 200
    assert get_response.json() == {
        "sessionId": str(service.session_id),
        "status": "active",
        "filename": "contract.pdf",
        "extension": ".pdf",
        "mediaType": "application/pdf",
        "sizeBytes": 10,
        "declaredSha256": "a" * 64,
        "partSizeBytes": 5,
        "expectedPartCount": 2,
        "expiresAt": get_response.json()["expiresAt"],
        "uploadedParts": [
            {
                "partNumber": 1,
                "sizeBytes": 5,
                "etag": '"etag-one"',
                "checksumSha256": CHECKSUM,
            }
        ],
    }
    assert "objectKey" not in get_response.json()
    assert "objectStoreUploadId" not in get_response.json()

    assert presign_response.status_code == 200
    assert presign_response.json() == {
        "partNumber": 2,
        "sizeBytes": 5,
        "checksumSha256": CHECKSUM,
        "url": "http://store.test/presigned-part",
        "headers": {"x-amz-checksum-sha256": CHECKSUM},
        "expiresInSeconds": 900,
    }
    assert len(service.presign_requests) == 1


async def test_session_errors_use_stable_status_codes_and_error_envelopes() -> None:
    principal = _principal()
    cases = (
        (UploadSessionNotFound(), 404, "upload_session_not_found"),
        (UploadSessionExpired(), 410, "upload_session_expired"),
        (UploadPartChecksumConflict(), 409, "upload_part_checksum_conflict"),
        (ObjectStoreUnavailable(), 503, "object_store_unavailable"),
    )

    for error, expected_status, expected_code in cases:
        service = StubUploadSessionService()
        service.error = error
        app = create_app(
            settings=ApiSettings(_env_file=None),
            checkers=[],
            principal_resolver=StubPrincipalResolver(principal),
            upload_creation_service=UnusedUploadCreationService(),
            upload_session_service=service,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                f"/api/upload-sessions/{service.session_id}",
                headers={
                    "Authorization": "Bearer token",
                    "X-Request-ID": "request-session-error",
                },
            )

        assert response.status_code == expected_status
        assert response.json() == {
            "error": {
                "code": expected_code,
                "message": error.message,
                "requestId": "request-session-error",
            }
        }


async def test_presign_requires_auth_and_strict_part_size() -> None:
    service = StubUploadSessionService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=UnusedUploadCreationService(),
        upload_session_service=service,
    )
    path = f"/api/upload-sessions/{service.session_id}/parts/1/presign"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_auth = await client.post(
            path,
            json={"sizeBytes": 5, "checksumSha256": CHECKSUM},
        )
        invalid_sizes = [
            await client.post(
                path,
                headers={"Authorization": "Bearer token"},
                json={"sizeBytes": value, "checksumSha256": CHECKSUM},
            )
            for value in (True, 5.0, "5")
        ]

    assert missing_auth.status_code == 401
    assert missing_auth.json()["error"]["code"] == "auth_missing"
    assert [response.status_code for response in invalid_sizes] == [422, 422, 422]
    assert service.presign_requests == []


def test_openapi_declares_resume_routes_and_typed_errors() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=UnusedUploadCreationService(),
        upload_session_service=StubUploadSessionService(),
    )
    schema = app.openapi()

    get_operation = schema["paths"]["/api/upload-sessions/{session_id}"]["get"]
    presign_operation = schema["paths"][
        "/api/upload-sessions/{session_id}/parts/{part_number}/presign"
    ]["post"]
    get_responses = get_operation["responses"]
    presign_responses = presign_operation["responses"]

    assert {"200", "404", "410", "500", "502", "503"} <= set(get_responses)
    assert {"200", "400", "409", "410", "422", "500", "502", "503"} <= set(presign_responses)
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert get_operation["security"] == [{"BearerAuth": []}]
    assert presign_operation["security"] == [{"BearerAuth": []}]
    assert presign_responses["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert get_responses["500"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
