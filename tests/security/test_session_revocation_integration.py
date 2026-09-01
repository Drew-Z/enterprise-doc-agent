from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_api.auth import DatabasePrincipalResolver, InvalidBearerToken, JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.audit import AuditEvent
from enterprise_doc_core.auth import LocalTokenRevocation, LocalTokenRevocationService
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory

pytestmark = pytest.mark.integration


async def test_local_jwt_logout_revokes_only_the_current_tenant_token_and_is_audited() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    settings = ApiSettings(_env_file=None)
    codec = JwtTokenCodec(settings.auth)
    service = LocalTokenRevocationService(session_factory=session_factory)
    issued_at = datetime.now(UTC)
    token = codec.issue_local_token(
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        now=issued_at,
    )
    claims = codec.decode(token)
    resolver = DatabasePrincipalResolver(session_factory=session_factory, codec=codec)

    try:
        principal = await resolver.resolve(token)
        assert principal.actor_id == str(context.actor_id)

        result = await service.revoke(
            tenant_id=claims.tenant_id,
            actor_id=claims.actor_id,
            token_id=claims.token_id,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
            request_id="logout-request",
            correlation_id="logout-correlation",
        )
        repeated = await service.revoke(
            tenant_id=claims.tenant_id,
            actor_id=claims.actor_id,
            token_id=claims.token_id,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
        )

        assert result.already_revoked is False
        assert repeated.already_revoked is True
        assert await service.is_revoked(tenant_id=claims.tenant_id, token_id=claims.token_id)
        with pytest.raises(InvalidBearerToken):
            await resolver.resolve(token)

        other_context = await _seed_agent_context(session_factory)
        assert not await service.is_revoked(
            tenant_id=other_context.tenant_id,
            token_id=claims.token_id,
        )

        async with session_factory() as session:
            actions = (
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.resource_id == result.revocation_id,
                    )
                )
            ).all()
        assert actions == ["auth.session.revoked"]
    finally:
        await engine.dispose()


async def test_expired_local_token_revocations_can_be_purged_in_bounded_batches() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = LocalTokenRevocationService(session_factory=session_factory)
    now = datetime.now(UTC)
    expired_id = "expired-token"

    try:
        await service.revoke(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            token_id=expired_id,
            issued_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        assert await service.purge_expired(now=now, limit=1) == 1
        async with session_factory() as session:
            remaining = await session.scalar(
                select(LocalTokenRevocation.id).where(
                    LocalTokenRevocation.tenant_id == context.tenant_id,
                    LocalTokenRevocation.token_id == expired_id,
                )
            )
        assert remaining is None
    finally:
        await engine.dispose()
