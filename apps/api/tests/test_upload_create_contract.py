from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.uploads.policy import UploadPolicyViolation
from enterprise_doc_core.uploads.service import (
    CreateUploadSessionInput,
    CreateUploadSessionResult,
    UploadIdempotencyConflict,
)


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class FailingPrincipalResolver:
    async def resolve(self, _: str) -> PrincipalContext:
        raise RuntimeError("database-password-must-not-leak")


class StubUploadCreationService:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed
        self.requests: list[tuple[PrincipalContext, str, CreateUploadSessionInput]] = []
        self.error: Exception | None = None
        self.session_id = uuid4()

    async def create(
        self,
        *,
        principal: PrincipalContext,
        idempotency_key: str,
        request: CreateUploadSessionInput,
    ) -> CreateUploadSessionResult:
        self.requests.append((principal, idempotency_key, request))
        if self.error is not None:
            raise self.error
        return CreateUploadSessionResult(
            session_id=self.session_id,
            status="initializing",
            filename=request.filename,
            extension=".pdf",
            media_type=request.media_type,
            size_bytes=request.size_bytes,
            declared_sha256=request.sha256,
            part_size_bytes=16 * 1024**2,
            expected_part_count=1,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            replayed=self.replayed,
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(
        tenant_id=str(uuid4()),
        actor_id=str(uuid4()),
        role="owner",
    )


def _body(*, size_bytes: int = 1024) -> dict[str, object]:
    return {
        "filename": "contract.pdf",
        "sizeBytes": size_bytes,
        "mediaType": "application/pdf",
        "sha256": "a" * 64,
    }


async def test_create_upload_requires_authentication_and_idempotency_key() -> None:
    service = StubUploadCreationService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_auth = await client.post(
            "/api/upload-sessions",
            headers={"Idempotency-Key": "create-1"},
            json=_body(),
        )
        missing_key = await client.post(
            "/api/upload-sessions",
            headers={"Authorization": "Bearer token"},
            json=_body(),
        )

    assert missing_auth.status_code == 401
    assert missing_auth.json()["error"]["code"] == "auth_missing"
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"
    assert service.requests == []


async def test_authentication_runs_before_request_body_validation() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=StubUploadCreationService(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions",
            headers={"Idempotency-Key": "create-1", "Content-Type": "application/json"},
            content="{",
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_missing"


async def test_create_upload_returns_a_camel_case_contract_and_request_id() -> None:
    principal = _principal()
    service = StubUploadCreationService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        upload_creation_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions",
            headers={
                "Authorization": "Bearer token",
                "Idempotency-Key": "create-1",
                "X-Request-ID": "request-create-1",
            },
            json=_body(),
        )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "request-create-1"
    assert response.json() == {
        "sessionId": str(service.session_id),
        "status": "initializing",
        "filename": "contract.pdf",
        "extension": ".pdf",
        "mediaType": "application/pdf",
        "sizeBytes": 1024,
        "declaredSha256": "a" * 64,
        "partSizeBytes": 16 * 1024**2,
        "expectedPartCount": 1,
        "expiresAt": response.json()["expiresAt"],
        "replayed": False,
    }
    assert "objectKey" not in response.json()
    assert service.requests == [
        (
            principal,
            "create-1",
            CreateUploadSessionInput(
                filename="contract.pdf",
                size_bytes=1024,
                media_type="application/pdf",
                sha256="a" * 64,
            ),
        )
    ]


async def test_idempotent_replay_and_conflict_have_stable_http_semantics() -> None:
    replay_service = StubUploadCreationService(replayed=True)
    replay_app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=replay_service,
    )
    conflict_service = StubUploadCreationService()
    conflict_service.error = UploadIdempotencyConflict()
    conflict_app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=conflict_service,
    )
    headers = {"Authorization": "Bearer token", "Idempotency-Key": "create-1"}

    async with AsyncClient(
        transport=ASGITransport(app=replay_app), base_url="http://test"
    ) as client:
        replay = await client.post("/api/upload-sessions", headers=headers, json=_body())
    async with AsyncClient(
        transport=ASGITransport(app=conflict_app), base_url="http://test"
    ) as client:
        conflict = await client.post("/api/upload-sessions", headers=headers, json=_body())

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "upload_idempotency_conflict"
    assert UUID(replay.json()["sessionId"]) == replay_service.session_id


async def test_request_validation_uses_the_shared_error_envelope() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=StubUploadCreationService(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions",
            headers={
                "Authorization": "Bearer token",
                "Idempotency-Key": "create-1",
                "X-Request-ID": "request-invalid-1",
            },
            json={"filename": "contract.pdf"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "request_validation_failed",
            "message": "The request payload is invalid.",
            "requestId": "request-invalid-1",
        }
    }


async def test_size_bytes_rejects_boolean_float_and_string_coercion() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=StubUploadCreationService(),
    )
    headers = {"Authorization": "Bearer token", "Idempotency-Key": "create-1"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.post(
                "/api/upload-sessions",
                headers=headers,
                json=_body(size_bytes=value),
            )
            for value in (True, 1.0, "1")
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert {response.json()["error"]["code"] for response in responses} == {
        "request_validation_failed"
    }


async def test_policy_size_limit_maps_to_413_and_unknown_paths_use_the_error_envelope() -> None:
    service = StubUploadCreationService()
    service.error = UploadPolicyViolation(
        code="upload_size_exceeded",
        message="The upload exceeds the configured size limit.",
    )
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=service,
    )
    headers = {"Authorization": "Bearer token", "Idempotency-Key": "create-1"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        too_large = await client.post("/api/upload-sessions", headers=headers, json=_body())
        missing = await client.get(
            "/api/does-not-exist",
            headers={"Authorization": "Bearer token"},
        )

    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "upload_size_exceeded"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "http_not_found"


def test_openapi_declares_replay_and_component_backed_error_responses() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=StubUploadCreationService(),
    )

    responses = app.openapi()["paths"]["/api/upload-sessions"]["post"]["responses"]

    assert {"200", "201", "409", "413", "422"} <= set(responses)
    assert responses["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


async def test_checker_override_keeps_default_business_database_dependencies() -> None:
    app = create_app(settings=ApiSettings(_env_file=None), checkers=[])
    resolver = app.state.principal_resolver
    service = app.state.upload_creation_service

    assert resolver.session_factory is not None
    assert service.session_factory is not None
    engine = service.session_factory.kw["bind"]
    await engine.dispose()


async def test_auth_middleware_maps_unexpected_resolver_errors_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=FailingPrincipalResolver(),
        upload_creation_service=StubUploadCreationService(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions",
            headers={"Authorization": "Bearer token", "Idempotency-Key": "create-1"},
            json=_body(),
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "database-password" not in response.text
    assert "database-password" not in caplog.text


async def test_cors_preflight_does_not_require_a_bearer_token() -> None:
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        upload_creation_service=StubUploadCreationService(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/upload-sessions",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Idempotency-Key,Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
