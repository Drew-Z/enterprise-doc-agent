from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunEvidence,
    AgentRunExecution,
    ApprovalRequest,
    ToolExecution,
)
from enterprise_doc_core.db import Base
from enterprise_doc_core.db.metadata import REGISTERED_MODELS

AGENT_TABLES = {
    "agent_runs",
    "agent_run_executions",
    "agent_run_events",
    "agent_run_evidence",
    "approval_requests",
    "tool_executions",
    "agent_artifacts",
}


def _constraint_names(table_name: str) -> set[str | None]:
    return {constraint.name for constraint in Base.metadata.tables[table_name].constraints}


def _index_names(table_name: str) -> set[str | None]:
    return {index.name for index in Base.metadata.tables[table_name].indexes}


def test_m4_models_are_registered_and_tenant_scoped() -> None:
    models = {
        AgentRun,
        AgentRunExecution,
        AgentRunEvent,
        AgentRunEvidence,
        ApprovalRequest,
        ToolExecution,
        AgentArtifact,
    }

    assert models <= set(REGISTERED_MODELS)
    assert AGENT_TABLES <= set(Base.metadata.tables)
    for table_name in AGENT_TABLES:
        assert "tenant_id" in Base.metadata.tables[table_name].columns


def test_agent_run_constraints_lock_idempotency_status_and_versions() -> None:
    table = Base.metadata.tables["agent_runs"]
    constraints = _constraint_names("agent_runs")
    indexes = _index_names("agent_runs")

    assert {
        "uq_agent_runs_tenant_id_idempotency_key",
        "uq_agent_runs_graph_thread_id",
        "ck_agent_runs_status_valid",
        "ck_agent_runs_next_event_seq_positive",
        "ck_agent_runs_current_execution_seq_non_negative",
    } <= constraints
    assert {
        "ix_agent_runs_tenant_id_status_created_at",
        "ix_agent_runs_tenant_id_document_version_id_created_at",
    } <= indexes
    assert table.columns.graph_version.type.length == 64
    assert table.columns.prompt_version.type.length == 64
    assert table.columns.tool_schema_version.type.length == 64


def test_execution_event_and_evidence_constraints_are_replay_safe() -> None:
    assert {
        "uq_agent_run_executions_run_id_sequence",
        "uq_agent_run_executions_job_id",
        "ck_agent_run_executions_kind_valid",
        "ck_agent_run_executions_sequence_non_negative",
        "ck_agent_run_executions_resume_shape_valid",
    } <= _constraint_names("agent_run_executions")
    assert "ix_agent_run_executions_tenant_id_run_id_sequence" in _index_names(
        "agent_run_executions"
    )

    assert {
        "uq_agent_run_events_run_id_seq",
        "ck_agent_run_events_seq_positive",
        "ck_agent_run_events_event_version_positive",
    } <= _constraint_names("agent_run_events")
    assert "ix_agent_run_events_tenant_id_run_id_seq" in _index_names("agent_run_events")

    assert {
        "uq_agent_run_evidence_run_id_chunk_id",
        "uq_agent_run_evidence_run_id_rank",
        "ck_agent_run_evidence_rank_positive",
        "ck_agent_run_evidence_rrf_score_positive",
    } <= _constraint_names("agent_run_evidence")


def test_approval_tool_and_artifact_constraints_are_exact_and_idempotent() -> None:
    approval_constraints = _constraint_names("approval_requests")
    approval_indexes = _index_names("approval_requests")
    assert {
        "uq_approval_requests_tenant_id_decision_idempotency_key",
        "uq_approval_requests_exact_target",
        "ck_approval_requests_status_valid",
        "ck_approval_requests_expiry_after_request",
    } <= approval_constraints
    assert {
        "uq_approval_requests_pending_run",
        "ix_approval_requests_tenant_id_run_id_status",
    } <= approval_indexes

    assert {
        "uq_tool_executions_tenant_id_idempotency_key",
        "ck_tool_executions_status_valid",
        "ck_tool_executions_target_pair_valid",
    } <= _constraint_names("tool_executions")
    assert "ix_tool_executions_tenant_id_run_id_created_at" in _index_names("tool_executions")

    artifact_table = Base.metadata.tables["agent_artifacts"]
    artifact_constraints = _constraint_names("agent_artifacts")
    assert {
        "uq_agent_artifacts_run_id_kind",
        "uq_agent_artifacts_object_location",
        "ck_agent_artifacts_status_valid",
        "ck_agent_artifacts_size_bytes_non_negative",
        "ck_agent_artifacts_content_metadata_pair",
    } <= artifact_constraints
    assert artifact_table.columns.object_key.type.length == 512


def test_agent_constraints_are_named_checks_or_uniques() -> None:
    for table_name in AGENT_TABLES:
        for constraint in Base.metadata.tables[table_name].constraints:
            if isinstance(constraint, (CheckConstraint, UniqueConstraint)):
                assert constraint.name is not None
