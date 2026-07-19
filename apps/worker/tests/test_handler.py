from __future__ import annotations

from uuid import uuid4

import pytest

from enterprise_doc_core.jobs import ClaimedJob, RetryDisposition
from enterprise_doc_worker.handler import (
    JobHandlerRouter,
    UnsupportedJobType,
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
