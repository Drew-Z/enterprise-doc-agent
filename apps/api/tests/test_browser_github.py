import asyncio
import base64
import hashlib
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from enterprise_doc_api.browser_auth.github import GitHubClient, GitHubEmailRequired, GitHubFailure
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_core.config import AppEnvironment

ORIGIN = "https://app.example.test"
VERIFIER = SecretStr("v" * 43)


def github_settings(**overrides: object) -> BrowserAuthSettings:
    return BrowserAuthSettings.model_validate(
        {
            "enabled": True,
            "provider": "github",
            "web_origin": ORIGIN,
            "client_id": "synthetic-github-client",
            "client_secret": "synthetic-client-secret",
        }
        | overrides
    )


def test_github_settings_use_official_oauth_without_oidc_metadata() -> None:
    settings = BrowserAuthSettings(
        enabled=True,
        provider="github",
        web_origin="https://app.example.test",
        client_id="synthetic-github-client",
        client_secret=SecretStr("synthetic-client-secret"),
    )
    settings.validate_environment(AppEnvironment.STAGING)
    assert settings.issuer == "https://github.com"
    assert settings.authorization_endpoint == "https://github.com/login/oauth/authorize"
    assert settings.token_endpoint == "https://github.com/login/oauth/access_token"
    assert settings.jwks_url is None
    assert settings.redirect_uri == "https://app.example.test/auth/callback"


async def test_github_code_pkce_and_fresh_verified_identity() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "github.com":
            assert str(request.url) == "https://github.com/login/oauth/access_token"
            assert request.method == "POST"
            assert request.headers["Accept"] == "application/json"
            assert parse_qs(request.content.decode()) == {
                "client_id": ["synthetic-github-client"],
                "client_secret": ["synthetic-client-secret"],
                "code": ["synthetic-code"],
                "redirect_uri": [ORIGIN + "/auth/callback"],
                "code_verifier": [VERIFIER.get_secret_value()],
            }
            return httpx.Response(
                200, json={"access_token": "synthetic_token", "token_type": "bearer"}
            )
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer synthetic_token"
        assert request.headers["User-Agent"]
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"id": 12345, "login": "mutable-name", "email": "decoy@example.test"}
            )
        assert request.url.path == "/user/emails"
        return httpx.Response(
            200,
            json=[
                {"email": "other@example.test", "primary": False, "verified": True},
                {"email": "Owner@Example.test", "primary": True, "verified": True},
            ],
        )

    client = GitHubClient(github_settings(), transport=httpx.MockTransport(respond))
    url = urlsplit(
        client.authorization_url(
            state=SecretStr("s" * 43), nonce=SecretStr("n" * 43), verifier=VERIFIER
        )
    )
    query = parse_qs(url.query)
    assert url.scheme + "://" + url.netloc + url.path == "https://github.com/login/oauth/authorize"
    assert query["state"] == ["s" * 43]
    assert query["scope"] == ["read:user user:email"]
    assert query["redirect_uri"] == [ORIGIN + "/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [
        base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).decode().rstrip("=")
    ]
    assert "nonce" not in query and "client_secret" not in query
    for _ in range(2):
        identity = await client.exchange(
            code=SecretStr("synthetic-code"),
            verifier=VERIFIER,
            nonce_digest="unused",
            started_at=datetime.now(UTC),
        )
        assert (identity.issuer, identity.subject, identity.email, identity.email_verified) == (
            "https://github.com",
            "12345",
            "owner@example.test",
            True,
        )
    assert len(requests) == 6  # No cached identity, token or email between sign-ins.


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "https://evil.example.test"},
        {"authorization_endpoint": "https://github.com.evil.test/login/oauth/authorize"},
        {"token_endpoint": "https://github.com/login/oauth/access_token?extra=1"},
        {"jwks_url": "https://github.com/jwks"},
        {"client_id": None},
        {"client_secret": None},
        {"client_secret": ""},
        {"provider": "linuxdo"},
    ],
)
def test_github_settings_reject_incomplete_or_wrong_provider_configuration(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        github_settings(**overrides)


async def exchange_with(transport: httpx.AsyncBaseTransport, **settings: object):
    return await GitHubClient(github_settings(**settings), transport=transport).exchange(
        code=SecretStr("synthetic-code"),
        verifier=VERIFIER,
        nonce_digest="unused",
        started_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "emails",
    [
        [],
        [{"email": "owner@example.test", "primary": True, "verified": False}],
        [{"email": "owner@example.test", "primary": True}],
        [{"email": "owner@example.test", "primary": True, "verified": "true"}],
        [{"email": "owner@example.test", "primary": True, "verified": 1}],
        [{"email": "owner@example.test", "primary": "true", "verified": True}],
        [{"email": "owner@example.test", "primary": 1, "verified": True}],
        [{"email": "other@example.test", "primary": False, "verified": True}],
        [
            {"email": "owner@example.test", "primary": True, "verified": False},
            {"email": "other@example.test", "primary": False, "verified": True},
        ],
        [
            {"email": "owner@example.test", "primary": True, "verified": True},
            {"email": "other@example.test", "primary": True, "verified": True},
        ],
    ],
)
async def test_github_never_infers_a_verified_primary_email(emails: list) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(
                200, json={"access_token": "synthetic_token", "token_type": "bearer"}
            )
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json={"id": 12345, "email": "public-decoy@example.test", "email_verified": True},
            )
        return httpx.Response(200, json=emails)

    with pytest.raises(GitHubEmailRequired):
        await exchange_with(httpx.MockTransport(respond))


@pytest.mark.parametrize(
    "stage,payload",
    [
        ("token", {}),
        ("token", []),
        ("token", {"access_token": "synthetic_token", "token_type": "mac"}),
        ("token", {"access_token": "bad\nheader", "token_type": "bearer"}),
        (
            "token",
            {"access_token": "synthetic_token", "token_type": "bearer", "error": "upstream-secret"},
        ),
        ("user", {}),
        ("user", []),
        ("user", {"id": True}),
        ("user", {"id": 0}),
        ("user", {"id": -1}),
        ("user", {"id": "123"}),
        ("emails", {}),
        ("emails", ["unexpected"]),
        ("emails", [{"primary": True, "verified": True, "email": None}]),
        ("emails", [{"primary": True, "verified": True, "email": "invalid"}]),
    ],
)
async def test_github_malformed_identity_or_token_is_rejected(stage: str, payload: object) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        current = (
            "token"
            if request.url.host == "github.com"
            else "user"
            if request.url.path == "/user"
            else "emails"
        )
        defaults = {
            "token": {"access_token": "synthetic_token", "token_type": "bearer"},
            "user": {"id": 12345},
        }
        return httpx.Response(200, json=payload if current == stage else defaults[current])

    with pytest.raises(
        GitHubFailure, match=r"^The identity provider could not verify this sign-in\.$"
    ):
        await exchange_with(httpx.MockTransport(respond))


@pytest.mark.parametrize("stage", ["token", "user", "emails"])
@pytest.mark.parametrize(
    "failure", ["redirect", "http_error", "invalid_json", "oversized", "timeout"]
)
async def test_github_http_failures_are_bounded_private_and_never_retried(
    stage: str, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        current = (
            "token"
            if request.url.host == "github.com"
            else "user"
            if request.url.path == "/user"
            else "emails"
        )
        calls.append(current)
        if current == stage:
            if failure == "timeout":
                raise httpx.ReadTimeout("upstream-private-detail", request=request)
            if failure == "redirect":
                return httpx.Response(
                    302, headers={"Location": "https://evil.example.test/token-leak"}
                )
            if failure == "http_error":
                return httpx.Response(403, json={"error": "upstream-private-detail"})
            return httpx.Response(
                200, content=b"x" * 262145 if failure == "oversized" else b"upstream-private-detail"
            )
        defaults = {
            "token": {"access_token": "synthetic_token", "token_type": "bearer"},
            "user": {"id": 12345},
        }
        return httpx.Response(200, json=defaults[current])

    with pytest.raises(GitHubFailure) as raised:
        await exchange_with(httpx.MockTransport(respond))
    assert calls == ["token", "user", "emails"][: ["token", "user", "emails"].index(stage) + 1]
    assert "upstream-private-detail" not in str(raised.value) + caplog.text
    assert "synthetic_token" not in str(raised.value) + caplog.text


async def test_github_exchange_has_one_total_deadline() -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        raise AssertionError("deadline was not enforced")

    with pytest.raises(GitHubFailure):
        await exchange_with(httpx.MockTransport(respond), oidc_timeout_seconds=0.01)
    assert calls == 1


@pytest.mark.parametrize("field", ["state", "verifier"])
def test_github_authorization_rejects_malformed_random_values(field: str) -> None:
    values = {"state": SecretStr("s" * 43), "nonce": SecretStr("n" * 43), "verifier": VERIFIER}
    values[field] = SecretStr("invalid")
    with pytest.raises(GitHubFailure):
        GitHubClient(github_settings()).authorization_url(**values)


async def test_github_email_pagination_uses_fixed_api_origin() -> None:
    pages: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(
                200, json={"access_token": "synthetic_token", "token_type": "bearer"}
            )
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 12345})
        assert request.url.host == "api.github.com"
        pages.append(request.url.params["page"])
        if len(pages) == 1:
            return httpx.Response(
                200,
                headers={"Link": '<https://evil.example.test>; rel="next"'},
                json=[
                    {"email": f"alias{i}@example.test", "primary": False, "verified": True}
                    for i in range(100)
                ],
            )
        return httpx.Response(
            200, json=[{"email": "owner@example.test", "primary": True, "verified": True}]
        )

    assert (await exchange_with(httpx.MockTransport(respond))).email == "owner@example.test"
    assert pages == ["1", "2"]
