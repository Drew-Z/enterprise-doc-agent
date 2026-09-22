from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from enterprise_doc_api.app import create_app
from enterprise_doc_api.browser_auth.github import GitHubClient
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.service import TenantAdmissionService

from .conftest import BrowserDatabase

pytestmark = pytest.mark.integration
ORIGIN = "https://app.example.test"
EMAIL = "github-owner@example.test"


class GitHubEndpoint:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, list[str]]] = {}
        self.verified: object = True
        self.subject = 12345
        self.requests = 0

    def authorize(self, location: str) -> dict[str, str]:
        url = urlsplit(location)
        assert (
            url.scheme + "://" + url.netloc + url.path == "https://github.com/login/oauth/authorize"
        )
        query = parse_qs(url.query)
        assert query["scope"] == ["read:user user:email"]
        assert query["redirect_uri"] == [ORIGIN + "/auth/callback"]
        assert query["code_challenge_method"] == ["S256"]
        code = uuid4().hex
        self.codes[code] = query
        return {
            "code": code,
            "state": query["state"][0],
            "iss": "https://github.com/login/oauth",
        }

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if request.url.host == "github.com":
            body = parse_qs(request.content.decode())
            query = self.codes.pop(body["code"][0])
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
                .decode()
                .rstrip("=")
            )
            assert query["code_challenge"] == [challenge]
            assert body["redirect_uri"] == query["redirect_uri"]
            return httpx.Response(
                200, json={"access_token": "synthetic_access", "token_type": "bearer"}
            )
        assert request.headers["Authorization"] == "Bearer synthetic_access"
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"id": self.subject, "login": "mutable-name", "email": EMAIL}
            )
        assert request.url.path == "/user/emails"
        return httpx.Response(
            200, json=[{"email": EMAIL, "primary": True, "verified": self.verified}]
        )


@pytest.fixture
async def github_http(
    browser_db: BrowserDatabase,
) -> AsyncIterator[tuple[httpx.AsyncClient, GitHubEndpoint]]:
    browser = BrowserAuthSettings(
        enabled=True,
        provider="github",
        web_origin=ORIGIN,
        client_id="synthetic-client",
        client_secret="synthetic-secret",
    )
    url = browser_db.engine.url.update_query_dict(
        {"options": f"-csearch_path={browser_db.schema},public"}
    )
    endpoint = GitHubEndpoint()
    app = create_app(
        settings=ApiSettings(
            _env_file=None,
            browser_auth=browser,
            database={"url": url.render_as_string(hide_password=False)},
        ),
        checkers=[],
        browser_identity_client=GitHubClient(
            browser, transport=httpx.MockTransport(endpoint.respond)
        ),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
        ) as client:
            yield client, endpoint


def session_headers(snapshot: dict) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Session-Context": snapshot["contextVersion"],
        "X-CSRF-Token": snapshot["csrfToken"],
    }


async def sign_in(client: httpx.AsyncClient, endpoint: GitHubEndpoint) -> dict:
    start = await client.get("/auth/login")
    assert start.status_code == 303
    params = endpoint.authorize(start.headers["location"])
    done = await client.get("/auth/callback", params=params)
    assert done.headers["location"] == ORIGIN + "/"
    cookie = done.headers["set-cookie"]
    assert all(part in cookie for part in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
    replay = await client.get("/auth/callback", params=params)
    assert replay.headers["location"].endswith("error=sign_in_failed")
    assert "set-cookie" not in replay.headers
    snapshot = await client.get("/auth/session")
    assert "synthetic_access" not in snapshot.text
    assert snapshot.json()["email"] == EMAIL
    assert snapshot.json()["loginProvider"] == "github"
    return snapshot.json()


async def test_github_login_admission_selection_logout_and_no_email_linking(
    github_http: tuple, browser_db: BrowserDatabase
) -> None:
    client, endpoint = github_http
    initial = await sign_in(client, endpoint)
    assert endpoint.requests == 3  # Replayed callback was consumed before exchange.
    assert initial["currentTenant"] is None
    assert (await client.get("/auth/tenants", headers=session_headers(initial))).json() == []
    prepared = prepare_admission_credential()
    await TenantAdmissionService(
        session_factory=browser_db.sessions, trusted_issuers=frozenset({"https://github.com"})
    ).issue(
        operator=PlatformAdmissionOperator("test-operator", "isolated GitHub acceptance"),
        request=AdmissionGrantRequest(
            recipient_email=EMAIL,
            issuer="https://github.com",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            quota_bytes=1000000,
            seat_limit=2,
        ),
        credential=prepared,
    )
    admitted = await client.post(
        "/auth/admission/accept",
        headers=session_headers(initial),
        json={"token": prepared.token.get_secret_value(), "tenantName": "GitHub demo"},
    )
    assert admitted.status_code == 200
    selected = await client.post(
        "/auth/tenant",
        headers=session_headers(initial),
        json={"tenantId": admitted.json()["tenantId"]},
    )
    assert selected.status_code == 200
    current = selected.json()
    assert current["loginProvider"] == "github"
    assert current["currentTenant"]["role"] == "owner"
    assert (await client.get("/api/session", headers=session_headers(current))).status_code == 200
    assert (await client.post("/auth/logout", headers=session_headers(current))).json() == {
        "revoked": True
    }
    assert (await client.get("/auth/session")).json() == {
        "status": "anonymous",
        "loginProvider": "github",
    }
    endpoint.subject += 1
    other = await sign_in(client, endpoint)
    assert other["currentTenant"] is None
    assert (await client.get("/auth/tenants", headers=session_headers(other))).json() == []


@pytest.mark.parametrize("verified", [False, "true", 1, None])
async def test_github_unverified_primary_email_returns_actionable_fixed_error(
    github_http: tuple, verified: object
) -> None:
    client, endpoint = github_http
    endpoint.verified = verified
    start = await client.get("/auth/login")
    params = endpoint.authorize(start.headers["location"])
    response = await client.get("/auth/callback", params=params)
    assert response.headers["location"] == ORIGIN + "/#/signin?error=github_email_required"
    assert "set-cookie" not in response.headers
    assert (await client.get("/auth/session")).json()["status"] == "anonymous"
    assert endpoint.requests == 3
    assert (
        (await client.get("/auth/callback", params=params))
        .headers["location"]
        .endswith("error=sign_in_failed")
    )
    assert endpoint.requests == 3


async def test_github_wrong_state_or_missing_cookie_does_not_exchange(github_http: tuple) -> None:
    client, endpoint = github_http
    start = await client.get("/auth/login")
    params = endpoint.authorize(start.headers["location"])
    wrong = await client.get("/auth/callback", params=params | {"state": "x" * 43})
    assert wrong.headers["location"].endswith("error=sign_in_failed")
    assert endpoint.requests == 0
    client.cookies.clear()
    missing = await client.get("/auth/callback", params=params)
    assert missing.headers["location"].endswith("error=sign_in_failed")
    assert endpoint.requests == 0


async def test_github_callback_without_optional_issuer_remains_supported(
    github_http: tuple,
) -> None:
    client, endpoint = github_http
    start = await client.get("/auth/login")
    params = endpoint.authorize(start.headers["location"])
    params.pop("iss")
    done = await client.get("/auth/callback", params=params)
    assert done.headers["location"] == ORIGIN + "/"
    assert (await client.get("/auth/session")).json()["status"] == "authenticated"
    assert endpoint.requests == 3


@pytest.mark.parametrize(
    "issuers",
    [
        ["https://github.com"],
        ["https://github.com/login/oauth/"],
        ["https://untrusted.example/login/oauth"],
        ["https://github.com/login/oauth", "https://github.com/login/oauth"],
    ],
)
async def test_github_callback_rejects_wrong_or_duplicate_issuer_without_consuming_attempt(
    github_http: tuple, issuers: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    client, endpoint = github_http
    start = await client.get("/auth/login")
    params = endpoint.authorize(start.headers["location"])
    query = [(key, value) for key, value in params.items() if key != "iss"]
    query.extend(("iss", value) for value in issuers)
    rejected = await client.get("/auth/callback", params=query)
    assert rejected.headers["location"].endswith("error=sign_in_failed")
    assert "set-cookie" not in rejected.headers
    assert endpoint.requests == 0
    events = [
        record.event_data for record in caplog.records if record.msg == "browser_sign_in_failed"
    ]
    assert events == [{"provider": "github", "reason": "issuer_mismatch"}]
    assert params["state"] not in caplog.text
    assert params["code"] not in caplog.text
    done = await client.get("/auth/callback", params=params)
    assert done.headers["location"] == ORIGIN + "/"
    assert endpoint.requests == 3
