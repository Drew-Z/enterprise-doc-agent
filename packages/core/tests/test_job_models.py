from __future__ import annotations

from enterprise_doc_core.db import Base
from enterprise_doc_core.jobs import is_allowed_job_diagnostic_code, mcp_diagnostic_code
from enterprise_doc_core.jobs.models import (
    Job,
    JobAttempt,
    JobAttemptStatus,
    JobEvent,
    JobStatus,
    OutboxEvent,
    OutboxEventStatus,
)


def test_m2_models_are_tenant_scoped_and_registered() -> None:
    assert {
        Job.__tablename__,
        JobAttempt.__tablename__,
        JobEvent.__tablename__,
        OutboxEvent.__tablename__,
    } <= set(Base.metadata.tables)
    for table in (Job.__table__, JobAttempt.__table__, JobEvent.__table__, OutboxEvent.__table__):
        assert "tenant_id" in table.columns


def test_m2_status_contracts_and_constraints_are_explicit() -> None:
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.RETRY_WAIT.value == "retry_wait"
    assert JobStatus.SUCCEEDED.value == "succeeded"
    assert JobStatus.DEAD.value == "dead"
    assert JobStatus.CANCELLED.value == "cancelled"
    assert JobAttemptStatus.ABANDONED.value == "abandoned"
    assert OutboxEventStatus.PUBLISHED.value == "published"

    job_constraints = {constraint.name for constraint in Job.__table__.constraints}
    assert "uq_jobs_tenant_id_idempotency_key" in job_constraints
    assert "uq_jobs_document_version_id" not in job_constraints
    assert "ck_jobs_status_valid" in job_constraints
    assert "ck_jobs_lease_pair" in job_constraints
    assert "ck_jobs_attempts_valid" in job_constraints

    event_constraints = {constraint.name for constraint in JobEvent.__table__.constraints}
    assert "uq_job_events_job_id_seq" in event_constraints

    diagnostic = JobAttempt.__table__.columns["diagnostic_code"]
    assert diagnostic.nullable is True
    assert diagnostic.type.length == 100


def test_m2_claim_indexes_are_stable() -> None:
    assert {
        "ix_jobs_claimable",
        "ix_jobs_document_version_id_created_at",
        "ix_jobs_lease_expiry",
        "ix_jobs_tenant_id_status_created_at",
    } <= {index.name for index in Job.__table__.indexes}
    assert "ix_outbox_events_publishable" in {index.name for index in OutboxEvent.__table__.indexes}


def test_job_diagnostic_codes_are_exact_and_operation_bound() -> None:
    assert is_allowed_job_diagnostic_code("grounding.citation_excerpt_not_verbatim")
    assert is_allowed_job_diagnostic_code("mcp.search_document.tool_input_invalid")
    assert not is_allowed_job_diagnostic_code("mcp.search_document.tool_input_invalid.extra")
    assert not is_allowed_job_diagnostic_code("mcp.unknown.tool_input_invalid")
    assert not is_allowed_job_diagnostic_code("raw provider message")
    assert (
        mcp_diagnostic_code(tool_name="search_document", subcode="tool_input_invalid")
        == "mcp.search_document.tool_input_invalid"
    )
    assert (
        mcp_diagnostic_code(tool_name="search_document", subcode="raw provider message")
        == "mcp.search_document.returned_error"
    )
    assert mcp_diagnostic_code(tool_name="unknown", subcode="tool_input_invalid") is None
