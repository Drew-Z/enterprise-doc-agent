from __future__ import annotations

import pytest
from sqlalchemy import delete, event, update
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_api.auth import (
    DatabasePrincipalResolver,
    InvalidBearerToken,
    JwtTokenCodec,
    PrincipalForbidden,
)
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.auth import LocalTokenRevocationService
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, Tenant, User

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "case",
    [
        "active",
        "membership_inactive",
        "tenant_inactive",
        "user_inactive",
        "missing",
        "revoked",
        "other_tenant_revocation",
    ],
)
async def test_principal_checks_share_one_query_without_weakening_denials(case: str) -> None:
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    context = await _seed_agent_context(sessions)
    codec = JwtTokenCodec(ApiSettings(_env_file=None).auth)
    token = codec.issue_local_token(tenant_id=context.tenant_id, actor_id=context.actor_id)
    claims = codec.decode(token)
    statements: list[str] = []

    def counted(conn, cursor, statement, parameters, execution_context, many):  # type: ignore[no-untyped-def]
        statements.append(statement.split()[0].upper())

    try:
        if case in ("revoked", "other_tenant_revocation"):
            target = context if case == "revoked" else await _seed_agent_context(sessions)
            await LocalTokenRevocationService(session_factory=sessions).revoke(
                tenant_id=target.tenant_id,
                actor_id=target.actor_id,
                token_id=claims.token_id,
                issued_at=claims.issued_at,
                expires_at=claims.expires_at,
            )
        async with sessions.begin() as session:
            if case in ("membership_inactive", "revoked"):
                await session.execute(
                    update(Membership)
                    .where(
                        Membership.tenant_id == context.tenant_id,
                        Membership.user_id == context.actor_id,
                    )
                    .values(is_active=False)
                )
            elif case == "tenant_inactive":
                await session.execute(
                    update(Tenant).where(Tenant.id == context.tenant_id).values(is_active=False)
                )
            elif case == "user_inactive":
                await session.execute(
                    update(User).where(User.id == context.actor_id).values(is_active=False)
                )
            elif case == "missing":
                await session.execute(
                    delete(Membership).where(
                        Membership.tenant_id == context.tenant_id,
                        Membership.user_id == context.actor_id,
                    )
                )
        event.listen(engine.sync_engine, "before_cursor_execute", counted)
        resolver = DatabasePrincipalResolver(session_factory=sessions, codec=codec)
        if case == "revoked":
            with pytest.raises(InvalidBearerToken):
                await resolver.resolve(token)
        elif case in ("active", "other_tenant_revocation"):
            principal = await resolver.resolve(token)
            assert principal.tenant_id == str(context.tenant_id)
            assert principal.actor_id == str(context.actor_id)
            assert principal.role == "owner"
        else:
            with pytest.raises(PrincipalForbidden):
                await resolver.resolve(token)
        assert statements == ["SELECT"]
    finally:
        if event.contains(engine.sync_engine, "before_cursor_execute", counted):
            event.remove(engine.sync_engine, "before_cursor_execute", counted)
        await engine.dispose()
