from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    GroundingValidationError,
    ModelCallTelemetry,
    ModelIdentity,
    ModelServerError,
)
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_worker.agent_handler import (
    AGENT_EXECUTE_JOB_TYPE,
    AgentExecutionContext,
    AgentExecutionContractMismatch,
    AgentExecutionHandler,
    AgentExecutionPayloadInvalid,
    AgentExecutionRuntimeError,
)


def _claim(**payload_overrides: Any) -> ClaimedJob:
    run_id = uuid4()
    payload: dict[str, Any] = {
        "payload_version": 1,
        "run_id": str(run_id),
        "execution_sequence": 0,
        "graph_thread_id": str(run_id),
        "graph_version": "m4.v1",
    }
    payload.update(payload_overrides)
    return ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=uuid4(),
        actor_id=uuid4(),
        worker_id="worker-1",
        lease_token=uuid4(),
        fencing_token=1,
        job_type=AGENT_EXECUTE_JOB_TYPE,
        payload=payload,
    )


class FakeLoader:
    def __init__(self, context: AgentExecutionContext) -> None:
        self.context = context
        self.claims: list[ClaimedJob] = []

    async def load(self, claim: ClaimedJob, payload: object) -> AgentExecutionContext:
        self.claims.append(claim)
        return self.context


class FakeExecutor:
    def __init__(self) -> None:
        self.contexts: list[AgentExecutionContext] = []

    async def __call__(self, context: AgentExecutionContext) -> None:
        self.contexts.append(context)


def _context(claim: ClaimedJob) -> AgentExecutionContext:
    return AgentExecutionContext(
        tenant_id=claim.tenant_id,
        actor_id=claim.actor_id,
        run_id=uuid4(),
        execution_id=uuid4(),
        execution_sequence=0,
        execution_kind="initial",
        job_id=claim.job_id,
        attempt_id=claim.attempt_id,
        attempt_number=claim.attempt_number,
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
        fencing_token=claim.fencing_token,
        document_version_id=uuid4(),
        task_type="question_answer",
        publish_requested=False,
        graph_thread_id=str(uuid4()),
        graph_version="m4.v1",
        run_status="pending",
        approval_request_id=None,
    )


@pytest.mark.asyncio
async def test_agent_handler_validates_payload_before_loading_and_executes_once() -> None:
    claim = _claim()
    context = _context(claim)
    loader = FakeLoader(context)
    executor = FakeExecutor()
    handler = AgentExecutionHandler(loader=loader, executor=executor)

    await handler(claim)

    assert loader.claims == [claim]
    assert executor.contexts == [context]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"payload_version": 2},
        {"run_id": "not-a-uuid"},
        {"execution_sequence": -1},
        {"graph_thread_id": ""},
        {"graph_version": ""},
        {"unexpected": True},
    ],
)
async def test_agent_handler_rejects_invalid_payload_before_database_access(
    overrides: dict[str, Any],
) -> None:
    claim = _claim(**overrides)
    loader = FakeLoader(_context(claim))
    executor = FakeExecutor()
    handler = AgentExecutionHandler(loader=loader, executor=executor)

    with pytest.raises(AgentExecutionPayloadInvalid):
        await handler(claim)

    assert loader.claims == []
    assert executor.contexts == []


@pytest.mark.asyncio
async def test_agent_handler_rejects_wrong_job_type() -> None:
    claim = replace(_claim(), job_type="document.ingest")
    loader = FakeLoader(_context(claim))
    executor = FakeExecutor()
    handler = AgentExecutionHandler(loader=loader, executor=executor)

    with pytest.raises(AgentExecutionContractMismatch):
        await handler(claim)

    assert loader.claims == []
    assert executor.contexts == []


@pytest.mark.asyncio
async def test_agent_handler_preserves_cooperative_cancellation() -> None:
    claim = _claim()
    context = _context(claim)
    loader = FakeLoader(context)

    async def cancelled(_: AgentExecutionContext) -> None:
        raise asyncio.CancelledError

    handler = AgentExecutionHandler(loader=loader, executor=cancelled)

    with pytest.raises(asyncio.CancelledError):
        await handler(claim)


@pytest.mark.asyncio
async def test_agent_handler_preserves_stable_runtime_error_classification() -> None:
    claim = _claim()
    context = _context(claim)
    loader = FakeLoader(context)

    class RetryableFailure(RuntimeError):
        code = "model_timeout"
        retryable = True
        diagnostic_code = "grounding.citation_excerpt_not_verbatim"

    async def failed(_: AgentExecutionContext) -> None:
        raise RetryableFailure

    handler = AgentExecutionHandler(loader=loader, executor=failed)

    with pytest.raises(AgentExecutionRuntimeError) as caught:
        await handler(claim)

    assert caught.value.code == "model_timeout"
    assert caught.value.retryable is True
    assert caught.value.diagnostic_code is None


@pytest.mark.asyncio
async def test_agent_handler_preserves_sanitized_model_failure_telemetry() -> None:
    claim = _claim()
    context = _context(claim)
    loader = FakeLoader(context)

    async def failed(_: AgentExecutionContext) -> None:
        raise ModelServerError(
            "raw provider body must not be projected",
            identity=ModelIdentity(
                provider="openai_compatible",
                model_name="fallback-model",
                model_version="2026-08-17",
            ),
            telemetry=ModelCallTelemetry(
                provider_request_count=2,
                fallback_count=1,
                breaker_state="open",
                fallback_trigger_code="model_server_error",
            ),
        )

    handler = AgentExecutionHandler(loader=loader, executor=failed)

    with pytest.raises(AgentExecutionRuntimeError) as caught:
        await handler(claim)

    assert caught.value.failure_metadata == {
        "model_failure": {
            "identity": {
                "provider": "openai_compatible",
                "model_name": "fallback-model",
                "model_version": "2026-08-17",
                "model_revision": None,
            },
            "telemetry": {
                "provider_request_count": 2,
                "usage_request_count": 0,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "repair_request_count": 0,
                "fallback_count": 1,
                "breaker_state": "open",
                "fallback_trigger_code": "model_server_error",
            },
        }
    }
    assert "raw provider body" not in str(caught.value.failure_metadata)


@pytest.mark.asyncio
async def test_agent_handler_propagates_only_typed_grounding_diagnostics() -> None:
    claim = _claim()
    context = _context(claim)
    loader = FakeLoader(context)

    async def failed(_: AgentExecutionContext) -> None:
        raise GroundingValidationError(
            "citation_not_in_candidates",
            "The model citation did not pass the deterministic authorization gate.",
            diagnostic_code="grounding.citation_excerpt_not_verbatim",
        )

    handler = AgentExecutionHandler(loader=loader, executor=failed)

    with pytest.raises(AgentExecutionRuntimeError) as caught:
        await handler(claim)

    assert caught.value.code == "citation_not_in_candidates"
    assert caught.value.retryable is False
    assert caught.value.diagnostic_code == "grounding.citation_excerpt_not_verbatim"
