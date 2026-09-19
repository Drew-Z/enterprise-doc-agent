from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx
import jwt
from pydantic import SecretStr

from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_core.admission.contracts import (
    VerifiedAdmissionIdentity,
    exact_identity_value,
    normalized_email,
)

_RANDOM_VALUE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MAX_RESPONSE_BYTES = 262144


class OidcFailure(Exception):
    def __init__(self) -> None:
        super().__init__("The identity provider could not verify this sign-in.")


class OidcClient:
    """Code exchange using explicit trusted endpoints; upstream tokens stay transient."""

    def __init__(
        self,
        settings: BrowserAuthSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("OIDC requires enabled browser authentication")
        self.settings = settings
        self.transport = transport

    def authorization_url(self, *, state: SecretStr, nonce: SecretStr, verifier: SecretStr) -> str:
        values = [value.get_secret_value() for value in (state, nonce, verifier)]
        if not all(_RANDOM_VALUE.fullmatch(value) for value in values):
            raise OidcFailure()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(values[2].encode()).digest())
        return (
            str(self.settings.authorization_endpoint)
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "response_mode": "query",
                    "client_id": self.settings.client_id,
                    "redirect_uri": self.settings.redirect_uri,
                    "scope": "openid email",
                    "state": values[0],
                    "nonce": values[1],
                    "code_challenge": challenge.decode().rstrip("="),
                    "code_challenge_method": "S256",
                }
            )
        )

    async def exchange(
        self,
        *,
        code: SecretStr,
        verifier: SecretStr,
        nonce_digest: str,
        started_at: datetime,
    ) -> VerifiedAdmissionIdentity:
        raw_code = code.get_secret_value()
        raw_verifier = verifier.get_secret_value()
        if (
            not raw_code
            or len(raw_code) > 2048
            or any(ord(c) < 33 or ord(c) == 127 for c in raw_code)
            or not _RANDOM_VALUE.fullmatch(raw_verifier)
        ):
            raise OidcFailure()
        auth = (
            httpx.BasicAuth(
                quote_plus(str(self.settings.client_id)),
                quote_plus(self.settings.client_secret.get_secret_value()),
            )
            if self.settings.client_secret is not None
            else None
        )
        try:
            async with asyncio.timeout(self.settings.oidc_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.settings.oidc_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    tokens = await _read_json(
                        client,
                        "POST",
                        str(self.settings.token_endpoint),
                        auth=auth,
                        data={
                            "grant_type": "authorization_code",
                            "code": raw_code,
                            "client_id": str(self.settings.client_id),
                            "redirect_uri": self.settings.redirect_uri,
                            "code_verifier": raw_verifier,
                        },
                    )
                    encoded = tokens.get("id_token")
                    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 16384:
                        raise ValueError("missing ID token")
                    keys = await _read_json(client, "GET", str(self.settings.jwks_url))
                    return self._verify(encoded, keys, nonce_digest, started_at)
        except (
            httpx.HTTPError,
            TimeoutError,
            jwt.PyJWTError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
        ):
            # Do not retain upstream response bodies, tokens or exception text.
            raise OidcFailure() from None

    def _verify(
        self, encoded: str, jwks: dict[str, Any], nonce_digest: str, started_at: datetime
    ) -> VerifiedAdmissionIdentity:
        header = jwt.get_unverified_header(encoded)
        algorithm, kid = header.get("alg"), header.get("kid")
        if (
            algorithm not in self.settings.algorithms
            or not isinstance(kid, str)
            or not 1 <= len(kid) <= 128
            or header.get("crit")
        ):
            raise ValueError("unsupported token header")
        raw_keys = jwks.get("keys")
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 32:
            raise ValueError("invalid key set")
        matches = [
            key
            for key in raw_keys
            if isinstance(key, dict)
            and key.get("kid") == kid
            and key.get("alg", algorithm) == algorithm
            and key.get("use", "sig") == "sig"
            and key.get("key_ops", ["verify"]) == ["verify"]
        ]
        if len(matches) != 1:
            raise ValueError("ambiguous or missing signing key")
        key = jwt.PyJWK.from_dict(matches[0], algorithm=algorithm).key
        claims = jwt.decode(
            encoded,
            key,
            algorithms=[algorithm],
            audience=self.settings.client_id,
            issuer=self.settings.issuer,
            leeway=self.settings.clock_leeway_seconds,
            options={
                "require": ["iss", "aud", "sub", "iat", "exp", "nonce", "email", "email_verified"]
            },
        )
        for name in ("iat", "exp", "nbf"):
            value = claims.get(name)
            if name == "nbf" and value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("invalid time claim")
        if (
            claims["exp"] <= claims["iat"]
            or claims["iat"] < started_at.timestamp() - self.settings.clock_leeway_seconds
        ):
            raise ValueError("stale ID token")
        audience = claims["aud"]
        if not isinstance(audience, (str, list)):
            raise ValueError("invalid audience")
        if (isinstance(audience, list) and len(audience) > 1) or "azp" in claims:
            if claims.get("azp") != self.settings.client_id:
                raise ValueError("invalid authorized party")
        nonce = claims["nonce"]
        if not isinstance(nonce, str) or not _RANDOM_VALUE.fullmatch(nonce):
            raise ValueError("invalid nonce")
        if not hmac.compare_digest(hashlib.sha256(nonce.encode()).hexdigest(), nonce_digest):
            raise ValueError("nonce mismatch")
        if claims["email_verified"] is not True:
            raise ValueError("unverified email")
        if not isinstance(claims["sub"], str) or not isinstance(claims["email"], str):
            raise ValueError("invalid identity claims")
        return VerifiedAdmissionIdentity(
            issuer=exact_identity_value(str(self.settings.issuer)),
            subject=exact_identity_value(claims["sub"]),
            email=normalized_email(claims["email"]),
            email_verified=True,
        )


async def _read_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    auth: httpx.BasicAuth | None = None,
    data: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with client.stream(
        method, url, auth=auth, data=data, headers={"Accept": "application/json"}
    ) as response:
        response.raise_for_status()
        body = bytearray()
        async for part in response.aiter_bytes():
            body.extend(part)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ValueError("OIDC response exceeds limit")
        decoded = json.loads(body)
        if not isinstance(decoded, dict):
            raise ValueError("OIDC response must be an object")
        return decoded
