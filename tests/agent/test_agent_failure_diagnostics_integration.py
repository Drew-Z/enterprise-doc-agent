from __future__ import annotations

from typing import Any, Never
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import OperationalError
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _request, _seed_agent_context, _service

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import AgentRun, DeterministicGroundedGateway
from enterprise_doc_core.config import AppEnvironment, DatabaseSettings, McpSettings, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.jobs import JobAttempt, JobRuntimeService, JobStatus
from enterprise_doc_worker.agent_handler import agent_failure_lock_key, project_agent_run_failure
from enterprise_doc_worker.agents import build_durable_agent_handler
from enterprise_doc_worker.handler import classify_job_error
from enterprise_doc_worker.mcp_client import McpStdioClient
from enterprise_doc_worker.queue import JobDeliveryConsumer, JobMessage

pytestmark = pytest.mark.integration


class UnavailableCheckpoint(InMemorySaver):
    async def aget_tuple(self, config: Any) -> Never:
        raise OperationalError("synthetic-private-checkpoint-detail")


class UnreachableGateway(DeterministicGroundedGateway):
    calls = 0

    async def generate(self, request: Any) -> Never:
        self.calls += 1
        raise AssertionError("model must not run before the checkpoint is loaded")


class UnreachableMcpClient(McpStdioClient):
    calls = 0

    async def call(self, **kwargs: Any) -> Never:
        self.calls += 1
        raise AssertionError("MCP must not run before the checkpoint is loaded")


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


async def test_checkpoint_failure_is_durable_and_visible_only_in_tenant_status() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    try:
        seeded = await _seed_agent_context(session_factory)
        service = _service(session_factory)
        runtime = JobRuntimeService(
            session_factory=session_factory,
            failure_lock_key=agent_failure_lock_key,
            failure_projector=project_agent_run_failure,
        )
        gateway = UnreachableGateway()
        mcp_client = UnreachableMcpClient(command="unreachable-mcp")
        handler = build_durable_agent_handler(
            session_factory=session_factory,
            model_settings=ModelSettings(),
            mcp_settings=McpSettings(),
            checkpointer=UnavailableCheckpoint(),
            gateway=gateway,
            mcp_client=mcp_client,
        )
        consumer = JobDeliveryConsumer(
            runtime=runtime,
            worker_id="checkpoint-diagnostic-worker",
            handler=handler,
            classify_error=classify_job_error,
        )
        created = await service.create(
            principal=seeded.principal,
            idempotency_key=f"checkpoint-diagnostic:{uuid4().hex}",
            request=_request(seeded),
        )
        message = JobMessage(
            job_id=created.job_id,
            tenant_id=seeded.tenant_id,
            event_id=uuid4(),
        )
        assert await consumer.handle(message) == "failed"
        assert await consumer.handle(message) == "duplicate_or_not_claimable"
        assert gateway.calls == 0
        assert mcp_client.calls == 0

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == created.job_id)
            )
        assert run is not None and run.status == "failed"
        assert run.error_code == "agent_execution_failed"
        assert run.provider_request_count is None
        assert run.provider_usage_request_count is None
        assert run.total_tokens is None
        assert attempt is not None and attempt.status == "permanent_failed"
        assert attempt.attempt_number == 1
        assert attempt.retryable is False
        assert attempt.error_class == "AgentExecutionRuntimeError"
        assert attempt.error_message == "The Agent execution failed."
        assert attempt.diagnostic_code == "agent.unexpected.database_operational_error"

        events = await service.list_events(run_id=created.run_id, tenant_id=seeded.tenant_id)
        assert [event.event_type for event in events] == [
            "run.created",
            "run.started",
            "run.finished",
        ]
        principal_resolver = StubPrincipalResolver(seeded.principal)
        app = create_app(
            settings=ApiSettings(
                _env_file=None, app_env=AppEnvironment.LOCAL, database=DatabaseSettings()
            ),
            checkers=[],
            principal_resolver=principal_resolver,
            agent_run_service=service,
        )
        app.state.job_runtime_service = runtime
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer synthetic-test-token"}
            run_path = f"/api/agent-runs/{created.run_id}"
            job_path = f"/api/jobs/{created.job_id}"
            assert (await client.get(run_path)).status_code == 401
            assert (await client.get(job_path)).status_code == 401
            run_response = await client.get(run_path, headers=headers)
            job_response = await client.get(job_path, headers=headers)
            assert run_response.status_code == 200
            assert job_response.status_code == 200
            run_status = run_response.json()
            job_status = job_response.json()
            assert run_status["status"] == "failed"
            assert run_status["errorCode"] == "agent_execution_failed"
            assert job_status["status"] == JobStatus.DEAD.value
            histories = (
                run_status["executions"][0]["attemptHistory"],
                job_status["attemptHistory"],
            )
            for history in histories:
                assert len(history) == 1
                assert history[0]["errorCode"] == "agent_execution_failed"
                assert history[0]["diagnosticCode"] == (
                    "agent.unexpected.database_operational_error"
                )
                assert "errorMessage" not in history[0]
            assert "synthetic-private" not in run_response.text + job_response.text

            principal_resolver.principal = PrincipalContext(
                tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner"
            )
            assert (await client.get(run_path, headers=headers)).status_code == 404
            assert (await client.get(job_path, headers=headers)).status_code == 404
    finally:
        await engine.dispose()
