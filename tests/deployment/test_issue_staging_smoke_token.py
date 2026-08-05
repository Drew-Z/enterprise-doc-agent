from __future__ import annotations

import sys
from contextlib import AbstractAsyncContextManager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import SecretStr

from enterprise_doc_api.config import ApiSettings, AuthSettings
from enterprise_doc_core.config import AppEnvironment

SCRIPT = Path(__file__).parents[2] / "scripts" / "issue_staging_smoke_token.py"
SPEC = spec_from_file_location("issue_staging_smoke_token_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
issue_token = module_from_spec(SPEC)
sys.modules[SPEC.name] = issue_token
SPEC.loader.exec_module(issue_token)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UUID("22222222-2222-4222-8222-222222222222")


class _Session:
    def __init__(self, role: str | None) -> None:
        self.role = role

    async def scalar(self, _statement: object) -> str | None:
        return self.role


class _SessionContext(AbstractAsyncContextManager[_Session]):
    def __init__(self, role: str | None) -> None:
        self.session = _Session(role)

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SessionFactory:
    def __init__(self, role: str | None) -> None:
        self.role = role

    def __call__(self) -> _SessionContext:
        return _SessionContext(self.role)


def _settings(environment: AppEnvironment) -> ApiSettings:
    return ApiSettings.model_construct(
        app_env=environment,
        auth=AuthSettings(
            issuer="enterprise-doc-agent-staging",
            audience="enterprise-doc-agent-api",
            signing_key=SecretStr("staging-signing-key-at-least-32-bytes"),
            token_ttl_seconds=900,
        ),
    )


def test_smoke_token_issuance_rejects_non_staging_environment() -> None:
    with pytest.raises(issue_token.StagingTokenNotAllowed, match="only in staging"):
        issue_token.ensure_staging(AppEnvironment.PRODUCTION)


@pytest.mark.asyncio
async def test_smoke_token_requires_active_membership() -> None:
    with pytest.raises(issue_token.ActiveSmokePrincipalRequired, match="active tenant membership"):
        await issue_token.issue_staging_smoke_token(
            settings=_settings(AppEnvironment.STAGING),
            session_factory=cast(Any, _SessionFactory(None)),
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
        )


@pytest.mark.asyncio
async def test_smoke_token_uses_requested_principal_and_staging_claims() -> None:
    settings = _settings(AppEnvironment.STAGING)
    token = await issue_token.issue_staging_smoke_token(
        settings=settings,
        session_factory=cast(Any, _SessionFactory("member")),
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
    )

    claims = issue_token.JwtTokenCodec(settings.auth).decode(token)
    assert claims.tenant_id == TENANT_ID
    assert claims.actor_id == ACTOR_ID
    assert claims.token_id
