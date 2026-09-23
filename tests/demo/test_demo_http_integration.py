from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from tests.browser_sessions.conftest import BrowserDatabase
from tests.browser_sessions.support import ISSUER, sign_in

from enterprise_doc_api.app import create_app
from enterprise_doc_api.browser_auth.http import SESSION_COOKIE
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.demo.models import DemoWorkspace
from enterprise_doc_core.demo.settings import DemoSettings
from enterprise_doc_core.uploads.models import UploadSession

pytestmark = pytest.mark.integration
ORIGIN = "https://app.example.test"


@pytest.fixture
async def demo_http(demo_db: BrowserDatabase) -> AsyncIterator[httpx.AsyncClient]:
    url = demo_db.engine.url.update_query_dict(
        {"options": f"-csearch_path={demo_db.schema},public"}
    )
    settings = ApiSettings(
        _env_file=None,
        database={"url": url.render_as_string(hide_password=False)},
        demo=DemoSettings(enabled=True),
        browser_auth=BrowserAuthSettings(
            enabled=True,
            web_origin=ORIGIN,
            issuer=ISSUER,
            authorization_endpoint="https://identity.example.test/authorize",
            token_endpoint="https://identity.example.test/token",
            jwks_url="https://identity.example.test/jwks",
            client_id="demo-test",
        ),
    )
    app = create_app(settings=settings, checkers=[])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=ORIGIN,
        ) as client,
    ):
        yield client


def headers(snapshot: dict) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Session-Context": snapshot["contextVersion"],
        "X-CSRF-Token": snapshot["csrfToken"],
    }


async def test_http_demo_has_cookie_csrf_replay_refresh_and_logout(
    demo_http: httpx.AsyncClient,
) -> None:
    anonymous = await demo_http.get("/auth/session")
    assert anonymous.json()["demoAvailable"] is True
    denied = await demo_http.post("/auth/demo", json={})
    assert denied.status_code == 403
    started = await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})
    assert started.status_code == 200, started.text
    snapshot = started.json()
    assert snapshot["demo"] is True and snapshot["email"] is None
    assert snapshot["currentTenant"]["name"] == "演示企业"
    cookie = started.headers["set-cookie"]
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
    assert "Domain=" not in cookie
    repeated = await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})
    assert repeated.json() == snapshot
    assert (await demo_http.get("/auth/session")).json() == snapshot
    assert (await demo_http.get("/api/documents")).status_code == 409
    assert (await demo_http.get("/api/documents", headers=headers(snapshot))).json() == []
    assert (await demo_http.get("/api/demo", headers=headers(snapshot))).json()["attemptsUsed"] == 0
    assert (
        await demo_http.post(
            "/api/presales",
            json={},
            headers={"Origin": ORIGIN, "X-Session-Context": snapshot["contextVersion"]},
        )
    ).status_code == 403
    assert (
        await demo_http.get(
            "/api/documents", headers={**headers(snapshot), "Authorization": "Bearer fake"}
        )
    ).status_code == 400
    assert (await demo_http.post("/auth/logout", headers=headers(snapshot))).json() == {
        "revoked": True
    }
    assert (await demo_http.get("/auth/session")).json()["status"] == "anonymous"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/audit-events"),
        ("POST", "/api/agent-runs"),
        ("GET", "/api/members"),
        ("POST", "/api/membership-invitations"),
        ("PUT", f"/api/documents/{uuid4()}/access"),
    ],
)
async def test_demo_cannot_access_administration_or_unbudgeted_models(
    demo_http: httpx.AsyncClient, method: str, path: str
) -> None:
    started = await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})
    denied = await demo_http.request(method, path, headers=headers(started.json()))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "demo_operation_forbidden"


async def test_guests_cannot_select_real_enterprise_or_use_other_context(
    demo_http: httpx.AsyncClient,
) -> None:
    first = (await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})).json()
    first_cookie = demo_http.cookies.get(SESSION_COOKIE)
    demo_http.cookies.clear()
    second = (await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})).json()
    assert first["currentTenant"]["tenantId"] != second["currentTenant"]["tenantId"]
    assert (await demo_http.get("/api/documents", headers=headers(first))).status_code == 409
    denied = await demo_http.post(
        "/auth/tenant",
        json={"tenantId": first["currentTenant"]["tenantId"]},
        headers=headers(second),
    )
    assert denied.status_code == 401
    demo_http.cookies.clear()
    restored = await demo_http.get(
        "/api/documents", headers={**headers(first), "Cookie": f"{SESSION_COOKIE}={first_cookie}"}
    )
    assert restored.status_code == 200


async def test_demo_does_not_replace_an_existing_formal_login(
    demo_http: httpx.AsyncClient, demo_db: BrowserDatabase
) -> None:
    service = BrowserSessionService(session_factory=demo_db.sessions, trusted_issuer=ISSUER)
    signed_in = await sign_in(service)
    demo_http.cookies.set(SESSION_COOKIE, signed_in.credential.get_secret_value())
    before = (await demo_http.get("/auth/session")).json()
    assert before["status"] == "authenticated" and before["email"]
    denied = await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "demo_signout_required"
    assert "set-cookie" not in denied.headers
    assert (await demo_http.get("/auth/session")).json() == before
    async with demo_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DemoWorkspace)) == 0


async def test_demo_rejects_oversized_upload_before_provisioning(
    demo_http: httpx.AsyncClient, demo_db: BrowserDatabase
) -> None:
    snapshot = (await demo_http.post("/auth/demo", json={}, headers={"Origin": ORIGIN})).json()
    denied = await demo_http.post(
        "/api/upload-sessions",
        headers={**headers(snapshot), "Idempotency-Key": str(uuid4())},
        json={
            "filename": "too-large.txt",
            "sizeBytes": 2097153,
            "mediaType": "text/plain",
            "sha256": "a" * 64,
        },
    )
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "demo_upload_limit"
    async with demo_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UploadSession)) == 0
