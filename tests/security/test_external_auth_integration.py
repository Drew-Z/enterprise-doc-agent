from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_api.auth import DatabaseExternalMembershipResolver
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership

pytestmark = pytest.mark.integration


async def test_external_membership_resolver_rechecks_live_membership_state() -> None:
    engine: AsyncEngine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    resolver = DatabaseExternalMembershipResolver(session_factory=session_factory)
    try:
        assert (
            await resolver.resolve_role(
                actor_id=context.actor_id,
                tenant_id=context.tenant_id,
            )
            == "owner"
        )

        async with session_factory.begin() as session:
            membership = await session.get(Membership, context.membership_id)
            assert membership is not None
            membership.is_active = False

        assert (
            await resolver.resolve_role(
                actor_id=context.actor_id,
                tenant_id=context.tenant_id,
            )
            is None
        )
    finally:
        await engine.dispose()
