from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.agents.router import _stream_agent_run_events
from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    AgentRunAttemptResult,
    AgentRunEventResult,
    AgentRunExecutionResult,
    AgentRunStatusResult,
)
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class DisconnectProbe:
    def __init__(self, *, disconnect_after: int | None = None) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.disconnect_after is not None and self.calls > self.disconnect_after


class StubAgentRunService:
    def __init__(self, *, status: str = "running") -> None:
        self.status = status
        self.run_id = uuid4()
        self.tenant_id = uuid4()
        self.events: list[AgentRunEventResult] = []
        self.list_calls: list[int] = []

    async def get_status(self, *, run_id: UUID, tenant_id: UUID) -> AgentRunStatusResult:
        assert run_id == self.run_id
        assert tenant_id == self.tenant_id
        return AgentRunStatusResult(
            run_id=run_id,
            tenant_id=tenant_id,
            document_version_id=uuid4(),
            task_type="question_answer",
            publish_requested=False,
            status=self.status,
            graph_version="m4.v1",
            prompt_version="m4.v1",
            model_provider="deterministic",
            model_name="deterministic",
            model_version=None,
            tool_schema_version="m4.v1",
            current_execution_seq=0,
            error_code=None,
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
            started_at=None,
            waiting_at=None,
            finished_at=None,
            cancelled_at=None,
            executions=(
                AgentRunExecutionResult(
                    execution_id=uuid4(),
                    sequence=0,
                    kind="initial",
                    job_id=uuid4(),
                    job_status="running",
                    attempts=1,
                    max_attempts=3,
                    cancel_requested=False,
                    attempt_history=(
                        AgentRunAttemptResult(
                            attempt_id=uuid4(),
                            attempt_number=1,
                            status="running",
                            worker_id="test",
                            started_at=datetime(2026, 7, 19, tzinfo=UTC),
                            heartbeat_at=None,
                            finished_at=None,
                            error_code=None,
                        ),
                    ),
                ),
            ),
        )

    async def list_events(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        after_seq: int = 0,
        limit: int = 100,
    ) -> tuple[AgentRunEventResult, ...]:
        assert run_id == self.run_id
        assert tenant_id == self.tenant_id
        self.list_calls.append(after_seq)
        return tuple(event for event in self.events if event.seq > after_seq)[:limit]


def _principal(tenant_id: UUID) -> PrincipalContext:
    return PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")


def _event(seq: int, event_type: str, payload: dict[str, object]) -> AgentRunEventResult:
    return AgentRunEventResult(
        event_id=uuid4(),
        seq=seq,
        event_type=event_type,
        event_version=1,
        public_payload=payload,
        created_at=datetime(2026, 7, 19, tzinfo=UTC),
    )


def _app(service: StubAgentRunService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal(service.tenant_id)),
        agent_run_service=service,
    )


@pytest.mark.asyncio
async def test_sse_replays_after_last_event_id_and_closes_on_terminal_event() -> None:
    service = StubAgentRunService(status="succeeded")
    service.events = [
        _event(
            1,
            "run.created",
            {
                "task_type": "question_answer",
                "document_version_id": uuid4(),
                "publish_requested": False,
            },
        ),
        _event(2, "run.started", {"status": "running"}),
        _event(3, "run.finished", {"status": "succeeded", "refusal_reason": None}),
    ]
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/agent-runs/{service.run_id}/events/stream",
            headers={"Authorization": "Bearer token", "Last-Event-ID": "1"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" not in response.text
    assert "id: 2" in response.text and "id: 3" in response.text
    assert response.text.index("id: 2") < response.text.index("id: 3")
    assert service.list_calls[0] == 1


@pytest.mark.asyncio
async def test_sse_rejects_invalid_cursor_before_streaming() -> None:
    service = StubAgentRunService()
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/agent-runs/{service.run_id}/events/stream",
            headers={"Authorization": "Bearer token", "Last-Event-ID": "not-a-seq"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "agent_event_cursor_invalid"


@pytest.mark.asyncio
async def test_sse_disconnect_stops_polling_and_heartbeat_is_comment_only() -> None:
    service = StubAgentRunService(status="running")
    probe = DisconnectProbe(disconnect_after=2)
    sleeps: list[float] = []
    clock_values = iter([0.0, 20.0, 20.0])

    async def fake_sleep(value: float) -> None:
        sleeps.append(value)

    output: list[str] = []
    async for frame in _stream_agent_run_events(
        service=service,
        request=probe,
        run_id=service.run_id,
        tenant_id=service.tenant_id,
        after_seq=0,
        initial_status="running",
        sleep=fake_sleep,
        monotonic=lambda: next(clock_values),
        heartbeat_seconds=15.0,
    ):
        output.append(frame)
    assert output == [": heartbeat\n\n"]
    assert sleeps == [0.1, 0.2]
    assert probe.calls >= 3
