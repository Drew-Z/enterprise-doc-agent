from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import func, select, update

from enterprise_doc_api.agents.router import stream_agent_run_events
from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.dependencies import resolve_request_principal
from enterprise_doc_api.browser_auth.http import SESSION_COOKIE
from enterprise_doc_api.browser_auth.oidc import OidcClient
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.agents import AgentRunEventResult
from enterprise_doc_core.browser_sessions.models import BrowserSession
from enterprise_doc_core.context import RequestContext, reset_request_context, set_request_context
from enterprise_doc_core.identity.models import Membership, Tenant

from .conftest import BrowserDatabase
from .support import IDENTITY, ISSUER, seed_tenants

pytestmark = pytest.mark.integration
ORIGIN = "https://app.example.test"


class SignedOidcEndpoint:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes: dict[str, dict[str, list[str]]] = {}
        self.invalid_nonce = False

    def authorize(self, location: str) -> dict[str, str]:
        query = parse_qs(urlsplit(location).query)
        assert query["redirect_uri"] == [ORIGIN + "/auth/callback"]
        code = uuid4().hex
        self.codes[code] = query
        return {"code": code, "state": query["state"][0], "iss": ISSUER}

    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [public | {"kid": "test", "alg": "RS256"}]})
        body = parse_qs(request.content.decode())
        query = self.codes.pop(body["code"][0])
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert query["code_challenge"] == [challenge]
        now = int(datetime.now(UTC).timestamp())
        encoded = jwt.encode(
            {
                "iss": ISSUER,
                "aud": "browser-http-test",
                "sub": IDENTITY.subject,
                "email": IDENTITY.email,
                "email_verified": True,
                "iat": now,
                "exp": now + 300,
                "nonce": "wrong" if self.invalid_nonce else query["nonce"][0],
            },
            self.key,
            "RS256",
            {"kid": "test"},
        )
        return httpx.Response(
            200,
            json={
                "id_token": encoded,
                "access_token": "synthetic-upstream-access",
                "refresh_token": "synthetic-upstream-refresh",
            },
        )


@pytest.fixture
async def signed_browser_app(browser_db: BrowserDatabase) -> AsyncIterator[tuple]:
    browser = BrowserAuthSettings(
        enabled=True,
        web_origin=ORIGIN,
        issuer=ISSUER,
        authorization_endpoint=ISSUER + "/authorize",
        token_endpoint=ISSUER + "/token",
        jwks_url=ISSUER + "/jwks",
        client_id="browser-http-test",
    )
    url = browser_db.engine.url.update_query_dict(
        {"options": f"-csearch_path={browser_db.schema},public"}
    )
    settings = ApiSettings(
        _env_file=None,
        browser_auth=browser,
        database={"url": url.render_as_string(hide_password=False)},
    )
    endpoint = SignedOidcEndpoint()
    app = create_app(
        settings=settings,
        checkers=[],
        browser_oidc_client=OidcClient(browser, transport=httpx.MockTransport(endpoint.respond)),
    )
    async with app.router.lifespan_context(app):
        yield app, endpoint


@pytest.fixture
async def browser_http(
    signed_browser_app: tuple,
) -> AsyncIterator[tuple[httpx.AsyncClient, SignedOidcEndpoint]]:
    app, endpoint = signed_browser_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
    ) as client:
        yield client, endpoint


async def login(client: httpx.AsyncClient, endpoint: SignedOidcEndpoint) -> dict:
    redirect = await client.get("/auth/login")
    assert redirect.status_code == 303
    callback = await client.get(
        "/auth/callback", params=endpoint.authorize(redirect.headers["location"])
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == ORIGIN + "/"
    cookie = callback.headers["set-cookie"]
    assert all(part in cookie for part in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
    assert "Domain=" not in cookie
    response = await client.get("/auth/session")
    assert response.status_code == 200
    assert "synthetic-upstream" not in response.text
    return response.json()


def headers(snapshot: dict) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Session-Context": snapshot["contextVersion"],
        "X-CSRF-Token": snapshot["csrfToken"],
    }


async def test_http_login_select_refresh_context_and_logout(
    browser_http: tuple, browser_db: BrowserDatabase
) -> None:
    client, endpoint = browser_http
    actor, tenant, second = await seed_tenants(browser_db)
    anonymous = await client.get("/auth/session")
    assert anonymous.json() == {"status": "anonymous"}
    initial = await login(client, endpoint)
    assert initial["currentTenant"] is None
    denied = await client.get("/api/session", headers=headers(initial))
    assert denied.status_code == 403
    choices = await client.get("/auth/tenants", headers=headers(initial))
    assert {item["tenantId"] for item in choices.json()} == {str(tenant), str(second)}
    selected = await client.post(
        "/auth/tenant", headers=headers(initial), json={"tenantId": str(tenant)}
    )
    assert selected.status_code == 200
    current = selected.json()
    assert current["currentTenant"]["actorId"] == str(actor)
    assert current["contextVersion"] != initial["contextVersion"]
    stale = await client.get("/api/session", headers=headers(initial))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "browser_context_stale"
    business = await client.get("/api/session", headers=headers(current))
    assert business.status_code == 200 and business.json()["tenantId"] == str(tenant)
    assert business.headers["cache-control"] == "no-store"
    refreshed = await client.get("/auth/session")
    assert refreshed.json()["contextVersion"] == current["contextVersion"]
    logout = await client.post("/auth/logout", headers=headers(current))
    assert logout.status_code == 200 and logout.json() == {"revoked": True}
    assert (await client.get("/auth/session")).json() == {"status": "anonymous"}


async def test_http_csrf_origin_mixed_duplicate_and_client_identity_are_rejected(
    browser_http: tuple, browser_db: BrowserDatabase
) -> None:
    client, endpoint = browser_http
    _, tenant, _ = await seed_tenants(browser_db)
    initial = await login(client, endpoint)
    for changed in (
        {"Origin": "https://evil.example.test"},
        {"X-CSRF-Token": "0" * 64},
        {"Origin": "null"},
    ):
        denied = await client.post(
            "/auth/tenant", headers=headers(initial) | changed, json={"tenantId": str(tenant)}
        )
        assert denied.status_code == 403
        assert "set-cookie" not in denied.headers
    missing = await client.post("/auth/tenant", json={"tenantId": str(tenant)})
    assert missing.status_code == 403
    mixed = await client.get(
        "/api/session", headers=headers(initial) | {"Authorization": "Bearer invalid-local-token"}
    )
    assert mixed.status_code == 400
    cookie = client.cookies.get(SESSION_COOKIE)
    duplicate = await client.get(
        "/api/session",
        headers=headers(initial)
        | {"Cookie": f"{SESSION_COOKIE}={cookie}; {SESSION_COOKIE}={cookie}"},
    )
    assert duplicate.status_code == 400
    duplicated_context = await client.get(
        "/api/session",
        headers=[*headers(initial).items(), ("X-Session-Context", initial["contextVersion"])],
    )
    assert duplicated_context.status_code == 409
    forged = await client.post(
        "/auth/admission/accept",
        headers=headers(initial),
        json={
            "token": "adm1_" + "x" * 43,
            "tenantName": "不能开通",
            "emailVerified": True,
            "issuer": ISSUER,
        },
    )
    assert forged.status_code == 422


async def test_http_nonce_failure_and_state_replay_create_no_session(
    browser_http: tuple, browser_db: BrowserDatabase
) -> None:
    client, endpoint = browser_http
    endpoint.invalid_nonce = True
    redirect = await client.get("/auth/login")
    params = endpoint.authorize(redirect.headers["location"])
    first = await client.get("/auth/callback", params=params)
    second = await client.get("/auth/callback", params=params)
    assert (
        first.headers["location"]
        == second.headers["location"]
        == ORIGIN + "/#/signin?error=sign_in_failed"
    )
    assert "set-cookie" not in first.headers and "set-cookie" not in second.headers
    assert (await client.get("/auth/session")).json() == {"status": "anonymous"}
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BrowserSession)) == 0


async def test_http_admission_uses_verified_session_and_replays_once(
    browser_http: tuple, browser_db: BrowserDatabase
) -> None:
    client, endpoint = browser_http
    snapshot = await login(client, endpoint)
    prepared = prepare_admission_credential()
    service = TenantAdmissionService(
        session_factory=browser_db.sessions, trusted_issuers=frozenset({ISSUER})
    )
    await service.issue(
        operator=PlatformAdmissionOperator("test-operator", "isolated acceptance"),
        request=AdmissionGrantRequest(
            recipient_email=IDENTITY.email,
            issuer=ISSUER,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            quota_bytes=1000000,
            seat_limit=2,
        ),
        credential=prepared,
    )
    payload = {"token": prepared.token.get_secret_value(), "tenantName": "浏览器开通企业"}
    accepted = await client.post("/auth/admission/accept", headers=headers(snapshot), json=payload)
    repeated = await client.post("/auth/admission/accept", headers=headers(snapshot), json=payload)
    assert accepted.status_code == repeated.status_code == 200
    assert accepted.json()["replayed"] is False and repeated.json()["replayed"] is True
    assert accepted.json()["tenantId"] == repeated.json()["tenantId"]
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
    selected = await client.post(
        "/auth/tenant", headers=headers(snapshot), json={"tenantId": accepted.json()["tenantId"]}
    )
    assert selected.status_code == 200
    assert selected.json()["currentTenant"]["name"] == "浏览器开通企业"


async def test_http_non_ascii_csrf_is_rejected_safely(browser_http: tuple) -> None:
    client, endpoint = browser_http
    initial = await login(client, endpoint)
    raw_headers = [
        (key.encode(), value.encode())
        for key, value in headers(initial).items()
        if key != "X-CSRF-Token"
    ]
    raw_headers.append((b"X-CSRF-Token", b"\xff" * 64))
    denied = await client.post("/auth/logout", headers=raw_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "browser_csrf_invalid"
    assert (await client.get("/auth/session")).json()["status"] == "authenticated"


class ControlledEvents:
    async def get_status(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(status="running")

    async def list_events(self, **_: object) -> tuple[AgentRunEventResult, ...]:
        return tuple(
            AgentRunEventResult(
                event_id=uuid4(),
                seq=seq,
                event_type="run.started",
                event_version=1,
                public_payload={"status": "running"},
                created_at=datetime.now(UTC),
            )
            for seq in (1, 2)
        )


@pytest.fixture
def stream_request_context():
    token = set_request_context(
        RequestContext(request_id="test-stream", correlation_id="test-stream")
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.mark.parametrize("change", ["logout", "expiry", "switch", "membership", "role"])
async def test_sse_rechecks_real_session_before_each_business_event(
    browser_http: tuple,
    signed_browser_app: tuple,
    browser_db: BrowserDatabase,
    change: str,
    stream_request_context: None,
) -> None:
    client, endpoint = browser_http
    app, _ = signed_browser_app
    actor, tenant, second = await seed_tenants(browser_db)
    initial = await login(client, endpoint)
    selected = (
        await client.post("/auth/tenant", headers=headers(initial), json={"tenantId": str(tenant)})
    ).json()
    raw_cookie = client.cookies.get(SESSION_COOKIE)
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/api/agent-runs/events/stream",
        "query_string": b"",
        "app": app,
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (
                headers(selected) | {"Cookie": f"{SESSION_COOKIE}={raw_cookie}"}
            ).items()
        ],
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive=receive)
    principal = await resolve_request_principal(request)
    app.state.agent_run_service = ControlledEvents()
    response = await stream_agent_run_events(uuid4(), request, principal, None)
    stream = response.body_iterator
    assert "id: 1" in str(await anext(stream))
    service = app.state.browser_session_service
    args = {"credential": SecretStr(raw_cookie), "context_version": selected["contextVersion"]}
    if change == "logout":
        await service.logout(**args)
    elif change == "switch":
        await service.select_tenant(**args, tenant_id=second)
    else:
        async with browser_db.sessions.begin() as session:
            if change == "expiry":
                await session.execute(
                    update(BrowserSession).values(
                        created_at=func.clock_timestamp() - timedelta(hours=2),
                        expires_at=func.clock_timestamp() - timedelta(hours=1),
                    )
                )
            else:
                await session.execute(
                    update(Membership)
                    .where(Membership.tenant_id == tenant, Membership.user_id == actor)
                    .values(
                        **({"is_active": False} if change == "membership" else {"role": "member"})
                    )
                )
    try:
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()
