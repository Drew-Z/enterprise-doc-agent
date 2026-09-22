from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from enterprise_doc_api.browser_auth.settings import (
    GITHUB_AUTHORIZATION_ENDPOINT,
    GITHUB_ISSUER,
    GITHUB_TOKEN_ENDPOINT,
    BrowserAuthSettings,
)
from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity, normalized_email

_RANDOM_VALUE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_BEARER_VALUE = re.compile(r"[A-Za-z0-9._~+/-]+={0,2}\Z")
_MAX_RESPONSE_BYTES = 262144
_API_ORIGIN = "https://api.github.com"


class GitHubFailure(Exception):
    def __init__(self) -> None:
        super().__init__("The identity provider could not verify this sign-in.")


class GitHubEmailRequired(GitHubFailure):
    """Only the provider's explicit primary/verified flags can satisfy admission."""


class GitHubClient:
    """Official OAuth code flow; access tokens are used only during this exchange."""

    def __init__(
        self,
        settings: BrowserAuthSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.enabled or settings.provider != "github":
            raise ValueError("GitHub OAuth requires enabled GitHub browser authentication")
        self.settings = settings
        self.transport = transport

    def authorization_url(self, *, state: SecretStr, nonce: SecretStr, verifier: SecretStr) -> str:
        # The shared session service also generates an OIDC nonce; OAuth does not send it.
        values = [value.get_secret_value() for value in (state, verifier)]
        if not all(_RANDOM_VALUE.fullmatch(value) for value in values):
            raise GitHubFailure()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(values[1].encode()).digest())
        return (
            GITHUB_AUTHORIZATION_ENDPOINT
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.client_id,
                    "redirect_uri": self.settings.redirect_uri,
                    "scope": "read:user user:email",
                    "state": values[0],
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
        # Attempt expiry, state and one-time consumption belong to BrowserSessionService.
        raw_code, raw_verifier = code.get_secret_value(), verifier.get_secret_value()
        if (
            not 1 <= len(raw_code) <= 2048
            or any(ord(c) < 33 or ord(c) == 127 for c in raw_code)
            or not _RANDOM_VALUE.fullmatch(raw_verifier)
        ):
            raise GitHubFailure()
        assert self.settings.client_secret is not None
        try:
            async with asyncio.timeout(self.settings.oidc_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.settings.oidc_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    headers={"User-Agent": "enterprise-doc-agent"},
                ) as client:
                    tokens = await _read_json(
                        client,
                        "POST",
                        GITHUB_TOKEN_ENDPOINT,
                        headers={"Accept": "application/json"},
                        data={
                            "client_id": str(self.settings.client_id),
                            "client_secret": self.settings.client_secret.get_secret_value(),
                            "code": raw_code,
                            "redirect_uri": self.settings.redirect_uri,
                            "code_verifier": raw_verifier,
                        },
                    )
                    if not isinstance(tokens, dict) or "error" in tokens:
                        raise ValueError("invalid token response")
                    token, token_type = tokens.get("access_token"), tokens.get("token_type")
                    if (
                        not isinstance(token, str)
                        or not 1 <= len(token) <= 4096
                        or not _BEARER_VALUE.fullmatch(token)
                        or not isinstance(token_type, str)
                        or token_type.lower() != "bearer"
                    ):
                        raise ValueError("invalid bearer token")
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "Authorization": "Bearer " + token,
                        "X-GitHub-Api-Version": "2022-11-28",
                    }
                    user = await _read_json(client, "GET", _API_ORIGIN + "/user", headers=headers)
                    if (
                        not isinstance(user, dict)
                        or type(user.get("id")) is not int
                        or user["id"] <= 0
                    ):
                        raise ValueError("invalid account identity")
                    subject = str(user["id"])
                    if len(subject) > 512:
                        raise ValueError("invalid account identity")
                    emails: list[dict[str, Any]] = []
                    # Use fixed URLs, never a provider-supplied pagination URL with credentials.
                    for page in range(1, 11):
                        batch = await _read_json(
                            client,
                            "GET",
                            f"{_API_ORIGIN}/user/emails?per_page=100&page={page}",
                            headers=headers,
                        )
                        if (
                            not isinstance(batch, list)
                            or len(batch) > 100
                            or any(not isinstance(item, dict) for item in batch)
                        ):
                            raise ValueError("invalid email response")
                        emails.extend(batch)
                        if len(batch) < 100:
                            break
                    else:
                        raise ValueError("email pagination exceeds limit")
                    primary = [item for item in emails if item.get("primary") is True]
                    if len(primary) != 1 or primary[0].get("verified") is not True:
                        raise GitHubEmailRequired()
                    email = primary[0].get("email")
                    if not isinstance(email, str):
                        raise ValueError("invalid email")
                    return VerifiedAdmissionIdentity(
                        issuer=GITHUB_ISSUER,
                        subject=subject,
                        email=normalized_email(email),
                        email_verified=True,
                    )
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, OverflowError):
            # No token, response body or upstream error description leaves this boundary.
            raise GitHubFailure() from None


async def _read_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: dict[str, str] | None = None,
) -> Any:
    async with client.stream(method, url, headers=headers, data=data) as response:
        response.raise_for_status()
        body = bytearray()
        async for part in response.aiter_bytes():
            body.extend(part)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ValueError("identity response exceeds limit")
        return json.loads(body)
