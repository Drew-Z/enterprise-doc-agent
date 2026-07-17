from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.auth import (
    DatabasePrincipalResolver,
    InvalidBearerToken,
    JwtTokenCodec,
    get_current_principal,
)
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.errors import register_error_handlers
from enterprise_doc_api.middleware import RequestContextMiddleware
from enterprise_doc_core.context import PrincipalContext, get_request_context


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal
        self.tokens: list[str] = []

    async def resolve(self, token: str) -> PrincipalContext:
        self.tokens.append(token)
        return self.principal


def _codec() -> JwtTokenCodec:
    settings = ApiSettings(_env_file=None).auth
    return JwtTokenCodec(settings)


def test_jwt_codec_round_trips_required_uuid_claims() -> None:
    codec = _codec()
    tenant_id = uuid4()
    actor_id = uuid4()
    token = codec.issue_local_token(
        tenant_id=tenant_id,
        actor_id=actor_id,
        now=datetime.now(UTC),
    )

    claims = codec.decode(token)

    assert claims.tenant_id == tenant_id
    assert claims.actor_id == actor_id
    assert claims.token_id


@pytest.mark.parametrize(
    "payload_override",
    [
        {"aud": "wrong-audience"},
        {"iss": "wrong-issuer"},
        {"tenant_id": "not-a-uuid"},
        {"sub": "not-a-uuid"},
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
def test_jwt_codec_rejects_invalid_claims_without_echoing_the_token(
    payload_override: dict[str, object],
) -> None:
    settings = ApiSettings(_env_file=None).auth
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()),
    }
    payload.update(payload_override)
    token = jwt.encode(payload, settings.signing_key.get_secret_value(), algorithm="HS256")

    with pytest.raises(InvalidBearerToken) as exc_info:
        JwtTokenCodec(settings).decode(token)

    assert token not in str(exc_info.value)


def test_jwt_codec_rejects_an_unapproved_algorithm_and_oversized_token() -> None:
    settings = ApiSettings(_env_file=None).auth
    now = datetime.now(UTC)
    payload = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()),
    }
    hs384_token = jwt.encode(
        payload,
        settings.signing_key.get_secret_value(),
        algorithm="HS384",
    )

    with pytest.raises(InvalidBearerToken):
        JwtTokenCodec(settings).decode(hs384_token)
    with pytest.raises(InvalidBearerToken):
        JwtTokenCodec(settings).decode("x" * (settings.max_token_length + 1))


async def test_auth_dependency_requires_exact_bearer_and_enriches_request_context() -> None:
    principal = PrincipalContext(tenant_id="tenant-1", actor_id="actor-1", role="owner")
    resolver = StubPrincipalResolver(principal)
    app = FastAPI()
    app.state.principal_resolver = resolver
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.get("/protected")
    async def protected(
        current: Annotated[PrincipalContext, Depends(get_current_principal)],
    ) -> dict[str, str | None]:
        context = get_request_context()
        return {
            "tenantId": current.tenant_id,
            "actorId": current.actor_id,
            "contextTenantId": context.principal.tenant_id
            if context and context.principal
            else None,
        }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/protected")
        malformed = await client.get("/protected", headers={"Authorization": "Basic value"})
        duplicate_valid_first = await client.get(
            "/protected",
            headers=[
                ("Authorization", "Bearer token-value"),
                ("Authorization", "Bearer second-token"),
            ],
        )
        duplicate_valid_last = await client.get(
            "/protected",
            headers=[
                ("Authorization", "Bearer second-token"),
                ("Authorization", "Bearer token-value"),
            ],
        )
        valid = await client.get("/protected", headers={"Authorization": "Bearer token-value"})

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert missing.json()["error"]["code"] == "auth_missing"
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "auth_invalid"
    assert duplicate_valid_first.status_code == 401
    assert duplicate_valid_first.json()["error"]["code"] == "auth_invalid"
    assert duplicate_valid_last.status_code == 401
    assert duplicate_valid_last.json()["error"]["code"] == "auth_invalid"
    assert valid.status_code == 200
    assert valid.json() == {
        "tenantId": "tenant-1",
        "actorId": "actor-1",
        "contextTenantId": "tenant-1",
    }
    assert resolver.tokens == ["token-value"]


def test_database_principal_resolver_is_constructed_from_a_session_factory() -> None:
    resolver = DatabasePrincipalResolver(session_factory=None, codec=_codec())

    assert resolver.session_factory is None
