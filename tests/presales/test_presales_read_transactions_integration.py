from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from enterprise_doc_api.auth import DatabasePrincipalResolver, JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_workflow_integration import workspace as workspace

pytestmark = pytest.mark.integration


async def test_authorized_packet_reads_reuse_prepared_queries(workspace):
    service, sessions, context, _, _, payload = workspace
    packet = await service.create(context.principal, payload, "read-cache")
    codec = JwtTokenCodec(ApiSettings(_env_file=None).auth)
    token = codec.issue_local_token(
        tenant_id=context.tenant_id, actor_id=context.actor_id, now=datetime.now(UTC)
    )
    resolver = DatabasePrincipalResolver(session_factory=sessions, codec=codec)
    for _ in range(8):
        principal = await resolver.resolve(token)
        assert (await service.get(principal, packet.id)) == packet

    # Inspect the server's real cache: reading through both auth and the service
    # must retain the prepared row/history query across request transactions.
    async with sessions.begin() as session:
        queries = (
            await session.scalars(text("SELECT statement FROM pg_prepared_statements"))
        ).all()
    assert any("FROM presales_rows" in query for query in queries)


async def test_read_only_scope_rejects_writes_and_restores_pool_state(workspace):
    from enterprise_doc_core.db import read_only_session

    _, sessions, context, _, _, _ = workspace
    statement = text("UPDATE tenants SET name = name WHERE id = :id")
    async with read_only_session(sessions) as session:
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
    with pytest.raises(DBAPIError) as failure:
        async with read_only_session(sessions) as session:
            await session.execute(statement, {"id": context.tenant_id})
    assert failure.value.orig.sqlstate == "25006"
    # The same pool remains writable after either success or failure; no global
    # autocommit/read-only setting is changed for normal business transactions.
    async with sessions.begin() as session:
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
        assert (await session.execute(statement, {"id": context.tenant_id})).rowcount == 1
