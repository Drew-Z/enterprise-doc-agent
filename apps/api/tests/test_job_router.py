from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from enterprise_doc_api.jobs.router import get_job
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.jobs import JobEventResult, JobStatusResult


class FakeJobService:
    def __init__(self) -> None:
        self.job_id = uuid4()
        self.tenant_id = uuid4()
        self.actor_id = uuid4()

    async def get_status(self, *, job_id, tenant_id):
        assert (job_id, tenant_id) == (self.job_id, self.tenant_id)
        return JobStatusResult(
            job_id=self.job_id,
            tenant_id=self.tenant_id,
            document_version_id=None,
            job_type="document.ingest",
            status="pending",
            priority=0,
            attempts=0,
            max_attempts=3,
            available_at=datetime(2026, 7, 18, tzinfo=UTC),
            last_error_code=None,
            started_at=None,
            finished_at=None,
            cancel_requested=False,
        )

    async def list_attempts(self, *, job_id, tenant_id):
        return ()

    async def list_events(self, *, job_id, tenant_id):
        return (
            JobEventResult(
                event_id=uuid4(),
                seq=1,
                event_type="job.created",
                status="pending",
                payload={"job_type": "document.ingest"},
                created_at=datetime(2026, 7, 18, tzinfo=UTC),
            ),
        )


async def test_job_status_response_is_tenant_scoped_and_serializable() -> None:
    service = FakeJobService()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(job_runtime_service=service))
    )
    principal = PrincipalContext(
        tenant_id=str(service.tenant_id),
        actor_id=str(service.actor_id),
        role="owner",
    )

    response = await get_job(service.job_id, request, principal)

    assert response.status == "pending"
    assert response.attempt_history == []
    assert response.events[0].event_type == "job.created"
    assert response.model_dump(mode="json")["tenant_id"] == str(service.tenant_id)
