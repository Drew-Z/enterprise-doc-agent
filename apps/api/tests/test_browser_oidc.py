from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from enterprise_doc_api.browser_auth.oidc import OidcClient, OidcFailure
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import AppEnvironment


def test_browser_oidc_requires_complete_explicit_configuration() -> None:
    with pytest.raises(ValidationError, match="browser authentication requires"):
        ApiSettings(browser_auth={"enabled": True}, _env_file=None)


def oidc_settings() -> BrowserAuthSettings:
    return BrowserAuthSettings(
        enabled=True,
        web_origin="https://app.example.test",
        issuer="https://idp.example.test",
        authorization_endpoint="https://idp.example.test/authorize",
        token_endpoint="https://idp.example.test/token",
        jwks_url="https://idp.example.test/jwks",
        client_id="browser-test-client",
    )


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("confidential", [False, True])
async def test_signed_oidc_code_exchange_verifies_identity_and_pkce(
    signing_key: rsa.RSAPrivateKey,
    confidential: bool,
) -> None:
    settings = oidc_settings()
    if confidential:
        settings.client_id = "browser:test+client"
        settings.client_secret = SecretStr("synthetic: secret/+")
    nonce, state, verifier = "n" * 43, "s" * 43, "v" * 43
    now = datetime.now(UTC)
    claims = {
        "iss": "https://idp.example.test",
        "aud": settings.client_id,
        "sub": "subject-one",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "nonce": nonce,
        "email": "Owner@Example.test",
        "email_verified": True,
    }
    public_key = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public_key.update(kid="test-key", alg="RS256", use="sig")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={"id_token": jwt.encode(claims, signing_key, "RS256", {"kid": "test-key"})},
            )
        return httpx.Response(200, json={"keys": [public_key]})

    client = OidcClient(settings, transport=httpx.MockTransport(respond))
    target = client.authorization_url(
        state=SecretStr(state), nonce=SecretStr(nonce), verifier=SecretStr(verifier)
    )
    query = parse_qs(urlsplit(target).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://app.example.test/auth/callback"]
    assert verifier not in target
    identity = await client.exchange(
        code=SecretStr("one-use-code"),
        verifier=SecretStr(verifier),
        nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),
        started_at=now,
    )
    assert identity.issuer == claims["iss"]
    assert identity.subject == "subject-one"
    assert identity.email == "owner@example.test"
    assert identity.email_verified is True
    body = parse_qs(requests[0].content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code_verifier"] == [verifier]
    assert body["redirect_uri"] == query["redirect_uri"]
    assert len(requests) == 2
    if confidential:
        expected = (
            quote_plus(str(settings.client_id))
            + ":"
            + quote_plus(settings.client_secret.get_secret_value())
        )
        assert (
            base64.b64decode(requests[0].headers["Authorization"].split(" ")[1]).decode()
            == expected
        )


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://untrusted.example.test"),
        ("aud", "another-client"),
        ("aud", ["browser-test-client", "another-client"]),
        ("azp", "another-client"),
        ("nonce", "m" * 43),
        ("email_verified", False),
        ("email_verified", "true"),
        ("email_verified", 1),
        ("email", "not-an-email"),
        ("sub", " subject-one "),
        ("sub", "x" * 513),
        ("iat", True),
        ("exp", 1),
        ("nbf", 9999999999),
        ("nonce", None),
    ],
)
async def test_oidc_rejects_invalid_signed_claims(
    signing_key: rsa.RSAPrivateKey, claim: str, value: object
) -> None:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": "https://idp.example.test",
        "aud": "browser-test-client",
        "sub": "subject-one",
        "email": "owner@example.test",
        "email_verified": True,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 300,
        "nonce": "n" * 43,
    }
    payload[claim] = value
    key = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    key.update(kid="test-key", alg="RS256")
    encoded = jwt.encode(payload, signing_key, "RS256", {"kid": "test-key"})
    client = OidcClient(
        oidc_settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"id_token": encoded} if request.url.path == "/token" else {"keys": [key]}
            )
        ),
    )
    with pytest.raises(OidcFailure) as caught:
        await client.exchange(
            code=SecretStr("one-use-code"),
            verifier=SecretStr("v" * 43),
            nonce_digest=hashlib.sha256(("n" * 43).encode()).hexdigest(),
            started_at=now,
        )
    assert "subject-one" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "failure",
    ["signature", "missing-key", "duplicate-key", "old-token", "redirect", "oversize", "timeout"],
)
async def test_oidc_protocol_failures_are_bounded_and_not_retried(
    signing_key: rsa.RSAPrivateKey, failure: str
) -> None:
    now = datetime.now(UTC)
    key = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    key.update(kid="test-key", alg="RS256")
    payload = {
        "iss": "https://idp.example.test",
        "aud": "browser-test-client",
        "sub": "subject-one",
        "email": "owner@example.test",
        "email_verified": True,
        "iat": int(now.timestamp()) - (120 if failure == "old-token" else 0),
        "exp": int(now.timestamp()) + 300,
        "nonce": "n" * 43,
    }
    signer = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if failure == "signature"
        else signing_key
    )
    encoded = jwt.encode(payload, signer, "RS256", {"kid": "test-key"})
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            await asyncio.sleep(0.1)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.example.test"})
        if failure == "oversize":
            return httpx.Response(200, content=b" " * 262145)
        keys = (
            [] if failure == "missing-key" else [key, key] if failure == "duplicate-key" else [key]
        )
        return httpx.Response(
            200, json={"id_token": encoded} if request.url.path == "/token" else {"keys": keys}
        )

    settings = oidc_settings()
    if failure == "timeout":
        settings.oidc_timeout_seconds = 0.01
    with pytest.raises(OidcFailure):
        await OidcClient(settings, transport=httpx.MockTransport(respond)).exchange(
            code=SecretStr("one-use-code"),
            verifier=SecretStr("v" * 43),
            nonce_digest=hashlib.sha256(("n" * 43).encode()).hexdigest(),
            started_at=now,
        )
    assert calls == (1 if failure in {"redirect", "oversize", "timeout"} else 2)


@pytest.mark.parametrize(
    "field", ["web_origin", "issuer", "authorization_endpoint", "token_endpoint", "jwks_url"]
)
def test_browser_settings_reject_remote_http_even_in_local(field: str) -> None:
    values = oidc_settings().model_dump()
    values[field] = "http://remote.example.test"
    with pytest.raises(ValueError, match="requires HTTPS"):
        BrowserAuthSettings(**values).validate_environment(AppEnvironment.LOCAL)


@pytest.mark.parametrize(
    "value",
    [
        "https://app.example.test/",
        "https://app.example.test/path",
        "https://app.example.test?secret=1",
        "https://user:password@app.example.test",
    ],
)
def test_browser_origin_is_exact(value: str) -> None:
    with pytest.raises(ValidationError):
        BrowserAuthSettings(**(oidc_settings().model_dump() | {"web_origin": value}))


def test_nonlocal_oidc_requires_confidential_client_and_rejects_symmetric_algorithms() -> None:
    with pytest.raises(ValueError, match="confidential client"):
        oidc_settings().validate_environment(AppEnvironment.PRODUCTION)
    with pytest.raises(ValidationError, match="asymmetric"):
        BrowserAuthSettings(algorithms=("HS256",))
