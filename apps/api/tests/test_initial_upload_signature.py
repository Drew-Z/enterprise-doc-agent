from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.uploads.router import router
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads import CreateUploadSessionResult, UploadSessionExpired


@pytest.mark.parametrize(
    ("include_signature", "transport", "state", "replayed", "expected"),
    [
        (True, "single_put", "active", False, True),
        (True, "single_put", "active", True, True),
        (None, "single_put", "active", False, False),
        (False, "single_put", "active", False, False),
        (True, "multipart", "active", False, False),
        (True, "single_put", "completed", True, False),
    ],
)
async def test_signature_is_opt_in_and_only_for_active_single_put(
    include_signature, transport, state, replayed, expected
):
    app, creator, signer, principal = make_app(transport, state, replayed)
    headers = {"Idempotency-Key": "same-operation"}
    params = (
        {} if include_signature is None else {"includeSignature": str(include_signature).lower()}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions", headers=headers, params=params, json=body(transport)
        )
    assert response.status_code == (200 if replayed else 201)
    assert ("initialUpload" in response.json()) is expected
    creator.create.assert_awaited_once()
    if expected:
        signer.presign_single_put.assert_awaited_once_with(
            principal=principal, session_id=creator.create.return_value.session_id
        )
        assert response.json()["initialUpload"] == {
            "url": "https://objects.example/signed",
            "headers": {"If-None-Match": "*"},
            "expiresInSeconds": 60,
        }
        assert response.headers["cache-control"] == "no-store"
    else:
        signer.presign_single_put.assert_not_awaited()


@pytest.mark.parametrize("failure", [ObjectStoreUnavailable(), UploadSessionExpired()])
async def test_signing_failure_preserves_successful_durable_creation(failure):
    app, creator, signer, _ = make_app("single_put", "active", False)
    signer.presign_single_put.side_effect = failure
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/upload-sessions?includeSignature=true",
            headers={"Idempotency-Key": "same"},
            json=body("single_put"),
        )
    assert response.status_code == 201
    assert "initialUpload" not in response.json()
    assert response.json()["sessionId"] == str(creator.create.return_value.session_id)
    signer.presign_single_put.assert_awaited_once()


def body(transport):
    return {
        "filename": "file.txt",
        "sizeBytes": 25,
        "mediaType": "text/plain",
        "sha256": "a" * 64,
        "transport": transport,
    }


def make_app(transport, state, replayed):
    principal = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")
    creator = SimpleNamespace(
        create=AsyncMock(
            return_value=CreateUploadSessionResult(
                session_id=uuid4(),
                status=state,
                filename="file.txt",
                extension=".txt",
                media_type="text/plain",
                size_bytes=25,
                declared_sha256="a" * 64,
                part_size_bytes=16_777_216,
                expected_part_count=1,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                replayed=replayed,
                transport=transport,
            )
        )
    )
    signer = SimpleNamespace(
        presign_single_put=AsyncMock(
            return_value=SimpleNamespace(
                url="https://objects.example/signed",
                headers={"If-None-Match": "*"},
                expires_in_seconds=60,
            )
        )
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.state.upload_creation_service = creator
    app.state.upload_session_service = signer
    return app, creator, signer, principal
