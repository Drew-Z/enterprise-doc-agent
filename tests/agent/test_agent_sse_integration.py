from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.agent.test_agent_run_integration import _request, _seed_agent_context

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    AgentRun,
    AgentRunService,
    AgentRunStatus,
    append_agent_run_event,
)
from enterprise_doc_core.config import AgentSettings, DatabaseSettings, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory

pytestmark = pytest.mark.integration


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


def _app(service: AgentRunService, principal: PrincipalContext):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        agent_run_service=service,
    )


async def _append_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    event_type: str,
    payload: dict[str, object],
) -> None:
    async with session_factory.begin() as session:
        await append_agent_run_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
        )


@pytest.mark.asyncio
async def test_real_sse_replays_after_api_restart_and_serializes_concurrent_sequences() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    seeded = await _seed_agent_context(session_factory)
    service = AgentRunService(
        session_factory=session_factory,
        agent_settings=AgentSettings(),
        model_settings=ModelSettings(),
    )
    try:
        created = await service.create(
            principal=seeded.principal,
            idempotency_key=f"sse-integration:{uuid4().hex}",
            request=_request(seeded),
        )
        await _append_event(
            session_factory,
            run_id=created.run_id,
            tenant_id=seeded.tenant_id,
            actor_id=seeded.actor_id,
            event_type="run.started",
            payload={"status": "running"},
        )
        await asyncio.gather(
            _append_event(
                session_factory,
                run_id=created.run_id,
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                event_type="run.resumed",
                payload={"status": "running"},
            ),
            _append_event(
                session_factory,
                run_id=created.run_id,
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                event_type="run.waiting_approval",
                payload={"status": "waiting_approval", "approval_id": uuid4()},
            ),
        )
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, created.run_id, with_for_update=True)
            assert run is not None
            run.status = AgentRunStatus.SUCCEEDED.value
            run.finished_at = datetime.now(UTC)
            await append_agent_run_event(
                session,
                tenant_id=seeded.tenant_id,
                run_id=created.run_id,
                event_type="run.finished",
                payload={"status": "succeeded", "refusal_reason": None},
                actor_id=seeded.actor_id,
            )

        principal = PrincipalContext(
            tenant_id=str(seeded.tenant_id), actor_id=str(seeded.actor_id), role="owner"
        )
        app_after_restart = _app(service, principal)
        async with AsyncClient(
            transport=ASGITransport(app=app_after_restart), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/agent-runs/{created.run_id}/events/stream",
                headers={"Authorization": "Bearer token", "Last-Event-ID": "1"},
            )
        assert response.status_code == 200
        ids = [
            int(line.removeprefix("id: "))
            for line in response.text.splitlines()
            if line.startswith("id: ")
        ]
        assert ids == sorted(ids)
        assert ids[0] == 2
        assert ids[-1] == 5
        assert "input_text" not in response.text
        assert "object_key" not in response.text

        foreign = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")
        foreign_app = _app(service, foreign)
        async with AsyncClient(
            transport=ASGITransport(app=foreign_app), base_url="http://test"
        ) as client:
            denied = await client.get(
                f"/api/agent-runs/{created.run_id}/events/stream",
                headers={"Authorization": "Bearer token"},
            )
        assert denied.status_code == 404
    finally:
        await engine.dispose()
