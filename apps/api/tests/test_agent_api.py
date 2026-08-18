from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    AgentRunAttemptResult,
    AgentRunEventResult,
    AgentRunExecutionResult,
    AgentRunIdempotencyConflict,
    AgentRunStatusResult,
    CreateAgentRunInput,
    CreateAgentRunResult,
    ReadyDocumentVersionResult,
)
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubAgentRunService:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed
        self.run_id = uuid4()
        self.job_id = uuid4()
        self.version_id = uuid4()
        self.requests: list[tuple[PrincipalContext, str, CreateAgentRunInput]] = []
        self.error: Exception | None = None

    async def create(self, *, principal, idempotency_key, request, **_):
        self.requests.append((principal, idempotency_key, request))
        if self.error is not None:
            raise self.error
        return CreateAgentRunResult(
            run_id=self.run_id,
            job_id=self.job_id,
            status="pending",
            replayed=self.replayed,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
        )

    async def get_status(self, *, run_id, tenant_id):
        return self._status(run_id=run_id, tenant_id=tenant_id)

    async def list_events(self, *, run_id, tenant_id, after_seq=0, limit=100):
        return (
            AgentRunEventResult(
                event_id=uuid4(),
                seq=1,
                event_type="run.created",
                event_version=1,
                public_payload={"publish_requested": False},
                created_at=datetime(2026, 7, 18, tzinfo=UTC),
            ),
        )

    async def cancel(self, *, run_id, tenant_id, actor_id):
        return self._status(run_id=run_id, tenant_id=tenant_id, status="cancelled")

    async def list_ready_document_versions(self, *, tenant_id):
        return (
            ReadyDocumentVersionResult(
                version_id=self.version_id,
                document_id=uuid4(),
                generation_id=uuid4(),
                filename="contract.pdf",
                size_bytes=1024,
                content_sha256="a" * 64,
                created_at=datetime(2026, 7, 18, tzinfo=UTC),
            ),
        )

    def _status(self, *, run_id, tenant_id, status="pending"):
        attempt_id = uuid4()
        execution_id = uuid4()
        return AgentRunStatusResult(
            run_id=run_id,
            tenant_id=tenant_id,
            document_version_id=self.version_id,
            task_type="question_answer",
            publish_requested=False,
            status=status,
            graph_version="m4.v1",
            prompt_version="m4.v1",
            model_provider="deterministic",
            model_name="deterministic-grounded",
            model_version=None,
            tool_schema_version="m4.v1",
            current_execution_seq=0,
            error_code=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            started_at=None,
            waiting_at=None,
            finished_at=None,
            cancelled_at=None,
            executions=(
                AgentRunExecutionResult(
                    execution_id=execution_id,
                    sequence=0,
                    kind="initial",
                    job_id=self.job_id,
                    job_status="running",
                    attempts=1,
                    max_attempts=3,
                    cancel_requested=False,
                    attempt_history=(
                        AgentRunAttemptResult(
                            attempt_id=attempt_id,
                            attempt_number=1,
                            status="running",
                            worker_id="worker-1",
                            started_at=datetime(2026, 7, 18, tzinfo=UTC),
                            heartbeat_at=None,
                            finished_at=None,
                            error_code=None,
                            diagnostic_code="grounding.citation_excerpt_not_verbatim",
                        ),
                    ),
                ),
            ),
            model_revision="revision-1",
            fallback_trigger_code="model_timeout",
            provider_request_count=2,
            provider_usage_request_count=2,
            prompt_tokens=30,
            completion_tokens=8,
            total_tokens=38,
            repair_request_count=1,
            fallback_count=1,
            breaker_state="open",
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


def _body(version_id) -> dict[str, object]:
    return {
        "documentVersionId": str(version_id),
        "taskType": "question_answer",
        "inputText": "What are the payment terms?",
        "publishRequested": False,
    }


async def test_agent_create_is_authenticated_idempotent_and_queue_only() -> None:
    principal = _principal()
    service = StubAgentRunService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        agent_run_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_auth = await client.post(
            "/api/agent-runs",
            headers={"Idempotency-Key": "agent-create-1"},
            json=_body(service.version_id),
        )
        missing_key = await client.post(
            "/api/agent-runs",
            headers={"Authorization": "Bearer token"},
            json=_body(service.version_id),
        )
        created = await client.post(
            "/api/agent-runs",
            headers={"Authorization": "Bearer token", "Idempotency-Key": "agent-create-1"},
            json=_body(service.version_id),
        )

    assert missing_auth.status_code == 401
    assert missing_key.status_code == 400
    assert created.status_code == 202
    assert created.json() == {
        "runId": str(service.run_id),
        "jobId": str(service.job_id),
        "status": "pending",
        "replayed": False,
        "createdAt": "2026-07-18T00:00:00Z",
    }
    assert service.requests == [
        (
            principal,
            "agent-create-1",
            CreateAgentRunInput(
                document_version_id=service.version_id,
                task_type="question_answer",
                input_text="What are the payment terms?",
                extraction_schema=None,
                publish_requested=False,
            ),
        )
    ]


async def test_agent_create_replay_conflict_and_validation_are_typed() -> None:
    headers = {"Authorization": "Bearer token", "Idempotency-Key": "agent-create-1"}
    replay_service = StubAgentRunService(replayed=True)
    replay_app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        agent_run_service=replay_service,
    )
    conflict_service = StubAgentRunService()
    conflict_service.error = AgentRunIdempotencyConflict()
    conflict_app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(_principal()),
        agent_run_service=conflict_service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=replay_app), base_url="http://test"
    ) as client:
        replay = await client.post(
            "/api/agent-runs", headers=headers, json=_body(replay_service.version_id)
        )
        invalid = await client.post(
            "/api/agent-runs",
            headers=headers,
            json={"documentVersionId": str(replay_service.version_id)},
        )
    async with AsyncClient(
        transport=ASGITransport(app=conflict_app), base_url="http://test"
    ) as client:
        conflict = await client.post(
            "/api/agent-runs", headers=headers, json=_body(conflict_service.version_id)
        )

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert invalid.status_code == 422
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "agent_run_idempotency_conflict"


async def test_agent_status_events_cancel_and_ready_versions_are_tenant_scoped() -> None:
    principal = _principal()
    service = StubAgentRunService()
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        agent_run_service=service,
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status_response = await client.get(f"/api/agent-runs/{service.run_id}", headers=headers)
        events = await client.get(f"/api/agent-runs/{service.run_id}/events", headers=headers)
        cancelled = await client.post(f"/api/agent-runs/{service.run_id}/cancel", headers=headers)
        versions = await client.get("/api/agent-runs/ready-document-versions", headers=headers)

    assert status_response.status_code == 200
    assert status_response.json()["runId"] == str(service.run_id)
    assert status_response.json()["modelRevision"] == "revision-1"
    assert status_response.json()["fallbackTriggerCode"] == "model_timeout"
    assert status_response.json()["providerRequestCount"] == 2
    assert status_response.json()["providerUsageRequestCount"] == 2
    assert status_response.json()["promptTokens"] == 30
    assert status_response.json()["completionTokens"] == 8
    assert status_response.json()["totalTokens"] == 38
    assert status_response.json()["repairRequestCount"] == 1
    assert status_response.json()["fallbackCount"] == 1
    assert status_response.json()["breakerState"] == "open"
    assert status_response.json()["executions"][0]["attemptHistory"][0]["workerId"] == ("worker-1")
    assert status_response.json()["executions"][0]["attemptHistory"][0]["diagnosticCode"] == (
        "grounding.citation_excerpt_not_verbatim"
    )
    assert "errorMessage" not in status_response.json()["executions"][0]["attemptHistory"][0]
    assert events.status_code == 200
    assert events.json()[0]["seq"] == 1
    assert cancelled.json()["status"] == "cancelled"
    assert versions.json()[0]["versionId"] == str(service.version_id)
