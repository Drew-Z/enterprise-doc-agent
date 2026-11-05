import base64
import hashlib

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.uploads.policy import UploadSettings


class PrincipalResolver:
    async def resolve(self, token):
        return PrincipalContext(tenant_id="tenant", actor_id="actor", role="owner")


def make_app(*, enabled=True):
    return create_app(
        settings=ApiSettings(_env_file=None, upload=UploadSettings(single_put_enabled=enabled)),
        checkers=[],
        principal_resolver=PrincipalResolver(),
    )


def payload(content=b"hello"):
    return {
        "filename": "note.txt",
        "mediaType": "text/plain",
        "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sizeBytes": 6}, 400),
        ({"sha256": "0" * 64}, 400),
        ({"contentBase64": "!not-base64!"}, 422),
        ({"contentBase64": ""}, 422),
        ({"sizeBytes": True}, 422),
        ({"sizeBytes": 1048577}, 422),
        ({"transport": "multipart"}, 422),
        ({"receipt": {"etag": "client-invented"}}, 422),
    ],
)
async def test_invalid_content_never_reaches_database(changes, expected):
    app = make_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/upload-sessions/content",
                headers={"Authorization": "Bearer test", "Idempotency-Key": "same-intent"},
                json=payload() | changes,
            )
    assert response.status_code == expected
    assert "client-invented" not in response.text


@pytest.mark.parametrize("enabled,authorized,expected", [(False, True, 404), (True, False, 401)])
async def test_disabled_or_unauthorized_content_does_not_read_body(enabled, authorized, expected):
    app = make_app(enabled=enabled)

    async def unread_body():
        pytest.fail("request body must not be read")
        yield b""

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/upload-sessions/content",
                headers={"Authorization": "Bearer test"} if authorized else {},
                content=unread_body(),
            )
    assert response.status_code == expected


@pytest.mark.parametrize("advertised", [None, "1", "2000000"])
async def test_wire_limit_is_enforced_for_chunked_and_false_content_length(advertised):
    app = make_app()
    consumed = 0

    async def stream():
        nonlocal consumed
        for _ in range(30):
            consumed += 1
            yield b" " * 100_000

    headers = {
        "Authorization": "Bearer test",
        "Idempotency-Key": "bounded",
        "Content-Type": "application/json",
    }
    if advertised is not None:
        headers["Content-Length"] = advertised
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/upload-sessions/content", headers=headers, content=stream()
            )
    assert response.status_code == 413
    assert consumed == (0 if advertised == "2000000" else 15)
