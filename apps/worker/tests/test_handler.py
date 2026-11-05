from __future__ import annotations

from uuid import uuid4

import pytest

from enterprise_doc_core.jobs import ClaimedJob, RetryDisposition
from enterprise_doc_worker.agent_handler import AgentExecutionStaleRun
from enterprise_doc_worker.handler import (
    DocumentParserRouter,
    JobHandlerRouter,
    UnsupportedJobType,
    UnsupportedParserVersion,
    classify_job_error,
)


def _claim(job_type: str) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=uuid4(),
        actor_id=uuid4(),
        worker_id="worker-router-test",
        lease_token=uuid4(),
        fencing_token=1,
        job_type=job_type,
        payload={},
    )


async def test_job_handler_router_dispatches_by_persisted_job_type() -> None:
    calls: list[tuple[str, ClaimedJob]] = []

    async def document_handler(claim: ClaimedJob) -> None:
        calls.append(("document", claim))

    async def agent_handler(claim: ClaimedJob) -> None:
        calls.append(("agent", claim))

    router = JobHandlerRouter(
        {
            "document.ingest": document_handler,
            "agent.execute": agent_handler,
        }
    )
    document_claim = _claim("document.ingest")
    agent_claim = _claim("agent.execute")

    await router(document_claim)
    await router(agent_claim)

    assert calls == [("document", document_claim), ("agent", agent_claim)]


async def test_job_handler_router_rejects_unknown_job_type_permanently() -> None:
    router = JobHandlerRouter({})

    with pytest.raises(UnsupportedJobType) as caught:
        await router(_claim("unknown.job"))

    assert caught.value.code == "unsupported_job_type"
    assert classify_job_error(caught.value) is RetryDisposition.PERMANENT


async def test_stale_terminal_agent_execution_is_cancelled() -> None:
    assert classify_job_error(AgentExecutionStaleRun()) is RetryDisposition.CANCELLED


async def test_parser_routing_uses_persisted_version_and_drains_legacy_jobs() -> None:
    calls: list[int] = []

    async def legacy(claim: ClaimedJob) -> None:
        calls.append(1)

    async def structured(claim: ClaimedJob) -> None:
        calls.append(2)

    router = DocumentParserRouter({1: legacy, 2: structured})
    claim = _claim("document.ingest")
    await router(claim)
    claim.payload["parser_version"] = 2
    await router(claim)
    assert calls == [1, 2]
    for invalid in (3, "2", True, None):
        claim.payload["parser_version"] = invalid
        with pytest.raises(UnsupportedParserVersion) as caught:
            await router(claim)
        assert classify_job_error(caught.value) is RetryDisposition.PERMANENT
    assert calls == [1, 2]
