from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from enterprise_doc_api.auth import (
    DatabaseExternalMembershipResolver,
    DatabasePrincipalResolver,
    ExternalIdentity,
    ExternalIdentityInvalid,
    ExternalPrincipalMappingError,
    ExternalPrincipalResolver,
    GroupRoleMapper,
    InvalidBearerToken,
    JwksExternalIdentityAdapter,
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


class StubExternalAdapter:
    def __init__(self, identity: ExternalIdentity) -> None:
        self.identity = identity

    async def decode(self, _: str) -> ExternalIdentity:
        return self.identity


class StubExternalMembershipResolver:
    def __init__(self, role: str = "owner") -> None:
        self.role = role

    async def resolve_role(self, *, actor_id: object, tenant_id: object) -> str | None:
        return self.role


class StubExternalIdentityBindingResolver:
    def __init__(self, actor_id: object) -> None:
        self.actor_id = actor_id

    async def resolve_actor_id(self, *, tenant_id: object, issuer: str, subject: str) -> object:
        return self.actor_id


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
        {"jti": " invalid-jti"},
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


async def test_database_external_membership_resolver_fails_clearly_without_database() -> None:
    resolver = DatabaseExternalMembershipResolver(session_factory=None)

    with pytest.raises(
        RuntimeError,
        match="external membership resolver session factory is unavailable",
    ):
        await resolver.resolve_role(actor_id=uuid4(), tenant_id=uuid4())


async def test_external_principal_resolver_maps_claims_and_groups() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    ).auth
    tenant_id = uuid4()
    actor_id = uuid4()
    resolver = ExternalPrincipalResolver(
        adapter=StubExternalAdapter(
            ExternalIdentity(
                issuer="https://idp.example.test",
                audience="enterprise-doc-agent",
                subject="user-123",
                actor_id=str(actor_id),
                tenant_id=str(tenant_id),
                groups=("tenant-owner",),
            )
        ),
        settings=settings,
        membership_resolver=StubExternalMembershipResolver(),
    )

    principal = await resolver.resolve("external-token")

    assert principal.tenant_id == str(tenant_id)
    assert principal.actor_id == str(actor_id)
    assert principal.role == "owner"


def test_group_role_mapper_rejects_ambiguous_and_conflicting_sources() -> None:
    mapper = GroupRoleMapper(role_claim_enabled=True)
    ambiguous = ExternalIdentity(
        issuer="issuer",
        audience="audience",
        subject="subject",
        actor_id=str(uuid4()),
        tenant_id=str(uuid4()),
        groups=("tenant-owner", "tenant-member"),
    )
    conflicting = ExternalIdentity(
        issuer="issuer",
        audience="audience",
        subject="subject",
        actor_id=str(uuid4()),
        tenant_id=str(uuid4()),
        groups=("tenant-member",),
        role="owner",
    )
    role_only = ExternalIdentity(
        issuer="issuer",
        audience="audience",
        subject="subject",
        actor_id=str(uuid4()),
        tenant_id=str(uuid4()),
        role="member",
    )

    assert mapper.map_role(ambiguous) is None
    assert mapper.map_role(conflicting) is None
    assert mapper.map_role(role_only) == "member"


async def test_external_role_claim_is_not_trusted_by_default() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    ).auth
    identity = ExternalIdentity(
        issuer="https://idp.example.test",
        audience="enterprise-doc-agent",
        subject="user-123",
        actor_id=str(uuid4()),
        tenant_id=str(uuid4()),
        role="owner",
    )

    resolver = ExternalPrincipalResolver(
        adapter=StubExternalAdapter(identity),
        settings=settings,
        membership_resolver=StubExternalMembershipResolver("owner"),
    )

    with pytest.raises(ExternalPrincipalMappingError):
        await resolver.resolve("external-token")


async def test_jwks_external_identity_adapter_verifies_oidc_claims_and_caches_keys() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    import base64

    def encoded(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    jwk = {
        "kty": "RSA",
        "kid": "key-1",
        "alg": "RS256",
        "use": "sig",
        "n": encoded(public_numbers.n),
        "e": encoded(public_numbers.e),
    }

    class StubJwksFetcher:
        calls = 0

        async def fetch(self, _: str) -> dict[str, object]:
            self.calls += 1
            return {"keys": [jwk]}

    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/.well-known/jwks.json",
        },
    ).auth
    tenant_id = uuid4()
    actor_id = uuid4()
    now = datetime.now(UTC)
    payload = {
        "iss": "https://idp.example.test",
        "aud": "enterprise-doc-agent",
        "sub": "subject-123",
        "actor_id": str(actor_id),
        "tenant_id": str(tenant_id),
        "groups": ["tenant-owner"],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-1"})
    fetcher = StubJwksFetcher()
    adapter = JwksExternalIdentityAdapter(settings=settings, fetcher=fetcher)

    identity = await adapter.decode(token)
    repeated = await adapter.decode(token)

    assert identity.actor_id == str(actor_id)
    assert identity.tenant_id == str(tenant_id)
    assert identity.groups == ("tenant-owner",)
    assert repeated.subject == "subject-123"
    assert fetcher.calls == 1

    fallback_payload = {**payload, "actor_id": None}
    fallback_token = jwt.encode(
        fallback_payload,
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    fallback_identity = await adapter.decode(fallback_token)
    assert fallback_identity.actor_id == "subject-123"


async def test_jwks_external_identity_adapter_rejects_wrong_kid_and_hs256() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/jwks",
        },
    ).auth

    class EmptyFetcher:
        async def fetch(self, _: str) -> dict[str, object]:
            return {"keys": []}

    adapter = JwksExternalIdentityAdapter(settings=settings, fetcher=EmptyFetcher())
    token = jwt.encode(
        {"sub": str(uuid4())},
        "not-a-jwk-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    with pytest.raises(ExternalIdentityInvalid) as exc_info:
        await adapter.decode(token)
    assert exc_info.value.code == "external_identity_invalid"


async def test_jwks_external_identity_adapter_refreshes_on_key_rotation() -> None:
    first_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk_for(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
        import base64

        public_numbers = private_key.public_key().public_numbers()

        def encoded(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        return {
            "kty": "RSA",
            "kid": kid,
            "alg": "RS256",
            "use": "sig",
            "n": encoded(public_numbers.n),
            "e": encoded(public_numbers.e),
        }

    first_jwk = jwk_for(first_private_key, "key-1")
    second_jwk = jwk_for(second_private_key, "key-2")

    class RotatingFetcher:
        calls = 0

        async def fetch(self, _: str) -> dict[str, object]:
            self.calls += 1
            return {"keys": [first_jwk] if self.calls == 1 else [second_jwk]}

    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/jwks",
        },
    ).auth
    now = datetime.now(UTC)
    payload = {
        "iss": "https://idp.example.test",
        "aud": "enterprise-doc-agent",
        "sub": "subject-rotation",
        "tenant_id": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(payload, second_private_key, algorithm="RS256", headers={"kid": "key-2"})
    fetcher = RotatingFetcher()
    adapter = JwksExternalIdentityAdapter(settings=settings, fetcher=fetcher)

    identity = await adapter.decode(token)

    assert identity.subject == "subject-rotation"
    assert fetcher.calls == 2


async def test_jwks_external_identity_adapter_sanitizes_fetcher_failures() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/jwks",
        },
    ).auth

    class FailingFetcher:
        async def fetch(self, _: str) -> dict[str, object]:
            raise RuntimeError("upstream details must not escape")

    adapter = JwksExternalIdentityAdapter(settings=settings, fetcher=FailingFetcher())
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {"sub": str(uuid4())},
        private_key,
        algorithm="RS256",
        headers={"kid": "failing-key"},
    )

    with pytest.raises(ExternalIdentityInvalid) as exc_info:
        await adapter.decode(token)

    assert exc_info.value.code == "external_identity_invalid"
    assert "upstream details" not in str(exc_info.value)


async def test_external_principal_resolver_rejects_group_membership_drift() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    ).auth
    resolver = ExternalPrincipalResolver(
        adapter=StubExternalAdapter(
            ExternalIdentity(
                issuer="https://idp.example.test",
                audience="enterprise-doc-agent",
                subject="user-123",
                actor_id=str(uuid4()),
                tenant_id=str(uuid4()),
                groups=("tenant-member",),
            )
        ),
        settings=settings,
        membership_resolver=StubExternalMembershipResolver("owner"),
    )

    with pytest.raises(ExternalPrincipalMappingError):
        await resolver.resolve("external-token")


async def test_external_principal_resolver_binds_non_uuid_oidc_subject() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    ).auth
    tenant_id = uuid4()
    actor_id = uuid4()
    resolver = ExternalPrincipalResolver(
        adapter=StubExternalAdapter(
            ExternalIdentity(
                issuer="https://idp.example.test",
                audience="enterprise-doc-agent",
                subject="idp-subject-123",
                actor_id="idp-subject-123",
                tenant_id=str(tenant_id),
                groups=("tenant-member",),
            )
        ),
        settings=settings,
        membership_resolver=StubExternalMembershipResolver("member"),
        identity_binding_resolver=StubExternalIdentityBindingResolver(actor_id),
    )

    principal = await resolver.resolve("external-token")

    assert principal.actor_id == str(actor_id)
    assert principal.tenant_id == str(tenant_id)
    assert principal.role == "member"


async def test_external_principal_resolver_never_bypasses_binding_for_uuid_subject() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    ).auth
    tenant_id = uuid4()
    untrusted_subject = uuid4()
    bound_actor_id = uuid4()
    resolver = ExternalPrincipalResolver(
        adapter=StubExternalAdapter(
            ExternalIdentity(
                issuer="https://idp.example.test",
                audience="enterprise-doc-agent",
                subject=str(untrusted_subject),
                actor_id=str(untrusted_subject),
                tenant_id=str(tenant_id),
                groups=("tenant-member",),
            )
        ),
        settings=settings,
        membership_resolver=StubExternalMembershipResolver("member"),
        identity_binding_resolver=StubExternalIdentityBindingResolver(bound_actor_id),
    )

    principal = await resolver.resolve("external-token")

    assert principal.actor_id == str(bound_actor_id)
    assert principal.actor_id != str(untrusted_subject)


def test_external_auth_requires_injected_resolver() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
        },
    )
    from enterprise_doc_api.app import create_app

    with pytest.raises(RuntimeError, match="no external principal resolver"):
        create_app(settings=settings, checkers=[])


def test_external_auth_builds_jwks_resolver_from_settings() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/.well-known/jwks.json",
        },
    )

    from enterprise_doc_api.app import create_app

    app = create_app(settings=settings, checkers=[])

    assert isinstance(app.state.principal_resolver, ExternalPrincipalResolver)
    assert isinstance(app.state.principal_resolver.adapter, JwksExternalIdentityAdapter)


def test_external_auth_builds_configured_group_role_mapper() -> None:
    settings = ApiSettings(
        _env_file=None,
        auth={
            "external_auth_enabled": True,
            "external_issuer": "https://idp.example.test",
            "external_audience": "enterprise-doc-agent",
            "external_jwks_url": "https://idp.example.test/.well-known/jwks.json",
            "external_owner_groups": ["security-admin"],
            "external_member_groups": ["knowledge-worker"],
            "external_role_claim_enabled": True,
        },
    )

    from enterprise_doc_api.app import create_app

    app = create_app(settings=settings, checkers=[])
    mapper = app.state.principal_resolver.role_mapper

    assert isinstance(mapper, GroupRoleMapper)
    assert mapper.owner_groups == frozenset({"security-admin"})
    assert mapper.member_groups == frozenset({"knowledge-worker"})
    assert mapper.role_claim_enabled is True


def test_external_auth_rejects_symmetric_algorithms_and_missing_trust_claims() -> None:
    with pytest.raises(ValidationError, match="issuer and audience"):
        ApiSettings(
            _env_file=None,
            auth={"external_auth_enabled": True},
        )
    with pytest.raises(ValidationError, match="algorithms are not allowed"):
        ApiSettings(
            _env_file=None,
            auth={
                "external_auth_enabled": True,
                "external_issuer": "https://idp.example.test",
                "external_audience": "enterprise-doc-agent",
                "external_algorithms": ["HS256"],
            },
        )
    with pytest.raises(ValidationError, match="must not overlap"):
        ApiSettings(
            _env_file=None,
            auth={
                "external_auth_enabled": True,
                "external_issuer": "https://idp.example.test",
                "external_audience": "enterprise-doc-agent",
                "external_owner_groups": ["operators"],
                "external_member_groups": ["operators"],
            },
        )
    with pytest.raises(ValidationError, match="duplicate group names"):
        ApiSettings(
            _env_file=None,
            auth={
                "external_owner_groups": ["operators", "operators"],
            },
        )


def test_scim_settings_normalize_tenant_keys_and_reject_ambiguous_tokens() -> None:
    tenant_id = uuid4()
    settings = ApiSettings(
        _env_file=None,
        auth={
            "scim_enabled": True,
            "scim_issuer": " https://idp.example.test/scim ",
            "scim_tenant_tokens": {str(tenant_id).upper(): "s" * 32},
        },
    ).auth

    assert settings.scim_issuer == "https://idp.example.test/scim"
    assert set(settings.scim_tenant_tokens) == {str(tenant_id)}

    with pytest.raises(ValidationError, match="SCIM issuer must not be blank"):
        ApiSettings(
            _env_file=None,
            auth={
                "scim_enabled": True,
                "scim_issuer": "   ",
                "scim_tenant_tokens": {str(tenant_id): "s" * 32},
            },
        )
    with pytest.raises(ValidationError, match="whitespace or control"):
        ApiSettings(
            _env_file=None,
            auth={
                "scim_enabled": True,
                "scim_issuer": "https://idp.example.test/scim",
                "scim_tenant_tokens": {
                    str(tenant_id): "s" * 31 + " ",
                },
            },
        )
    with pytest.raises(ValidationError, match="must be unique"):
        ApiSettings(
            _env_file=None,
            auth={
                "scim_tenant_tokens": {
                    str(tenant_id): "s" * 32,
                    str(tenant_id).upper(): "o" * 32,
                },
            },
        )
