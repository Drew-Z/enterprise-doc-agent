from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete

from enterprise_doc_api.auth import DatabasePrincipalResolver, JwtTokenCodec, PrincipalForbidden
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User


@pytest.mark.integration
async def test_database_principal_resolution_revalidates_active_membership() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    tenant_id = uuid4()
    actor_id = uuid4()
    membership_id = uuid4()
    try:
        async with session_factory.begin() as session:
            session.add(
                Tenant(
                    id=tenant_id,
                    name="Auth Integration Tenant",
                    slug=f"auth-{tenant_id}",
                    quota_bytes=10 * 1024**3,
                )
            )
            session.add(User(id=actor_id, email=f"auth-{actor_id}@example.test"))
            await session.flush()
            session.add(
                Membership(
                    id=membership_id,
                    tenant_id=tenant_id,
                    user_id=actor_id,
                    role=MembershipRole.OWNER.value,
                )
            )

        codec = JwtTokenCodec(settings.auth)
        resolver = DatabasePrincipalResolver(session_factory=session_factory, codec=codec)
        token = codec.issue_local_token(
            tenant_id=tenant_id,
            actor_id=actor_id,
            now=datetime.now(UTC),
        )

        principal = await resolver.resolve(token)
        assert principal.tenant_id == str(tenant_id)
        assert principal.actor_id == str(actor_id)
        assert principal.role == MembershipRole.OWNER.value

        async with session_factory.begin() as session:
            membership = await session.get(Membership, membership_id)
            assert membership is not None
            membership.is_active = False

        with pytest.raises(PrincipalForbidden):
            await resolver.resolve(token)
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Membership).where(Membership.id == membership_id))
            await session.execute(delete(User).where(User.id == actor_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await engine.dispose()
