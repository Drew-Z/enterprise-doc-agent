from __future__ import annotations

import pytest

from enterprise_doc_core.agents.models import (
    AgentArtifactStatus,
    AgentRunStatus,
    ApprovalRequestStatus,
    ToolExecutionStatus,
)
from enterprise_doc_core.agents.state import (
    AgentArtifactEvent,
    AgentRunTransitionEvent,
    ApprovalRequestEvent,
    InvalidStateTransition,
    ToolExecutionEvent,
    is_agent_run_terminal,
    transition_agent_artifact,
    transition_agent_run,
    transition_approval_request,
    transition_tool_execution,
)


@pytest.mark.parametrize(
    ("source", "event", "expected"),
    [
        (AgentRunStatus.PENDING, AgentRunTransitionEvent.START, AgentRunStatus.RUNNING),
        (
            AgentRunStatus.RUNNING,
            AgentRunTransitionEvent.WAIT_FOR_APPROVAL,
            AgentRunStatus.WAITING_APPROVAL,
        ),
        (
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunTransitionEvent.RESUME,
            AgentRunStatus.RUNNING,
        ),
        (
            AgentRunStatus.RUNNING,
            AgentRunTransitionEvent.SUCCEED,
            AgentRunStatus.SUCCEEDED,
        ),
        (AgentRunStatus.RUNNING, AgentRunTransitionEvent.REFUSE, AgentRunStatus.REFUSED),
        (AgentRunStatus.RUNNING, AgentRunTransitionEvent.FAIL, AgentRunStatus.FAILED),
        (AgentRunStatus.PENDING, AgentRunTransitionEvent.CANCEL, AgentRunStatus.CANCELLED),
        (AgentRunStatus.RUNNING, AgentRunTransitionEvent.CANCEL, AgentRunStatus.CANCELLED),
        (
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunTransitionEvent.CANCEL,
            AgentRunStatus.CANCELLED,
        ),
        (
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunTransitionEvent.REJECT,
            AgentRunStatus.REJECTED,
        ),
        (
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunTransitionEvent.EXPIRE,
            AgentRunStatus.EXPIRED,
        ),
    ],
)
def test_agent_run_legal_transitions(
    source: AgentRunStatus,
    event: AgentRunTransitionEvent,
    expected: AgentRunStatus,
) -> None:
    assert transition_agent_run(source, event) is expected


@pytest.mark.parametrize(
    "terminal",
    [
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.REFUSED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.EXPIRED,
    ],
)
def test_agent_run_terminal_states_cannot_reopen(terminal: AgentRunStatus) -> None:
    assert is_agent_run_terminal(terminal) is True
    with pytest.raises(InvalidStateTransition):
        transition_agent_run(terminal, AgentRunTransitionEvent.START)


@pytest.mark.parametrize(
    ("source", "event"),
    [
        (AgentRunStatus.PENDING, AgentRunTransitionEvent.SUCCEED),
        (AgentRunStatus.RUNNING, AgentRunTransitionEvent.RESUME),
        (AgentRunStatus.WAITING_APPROVAL, AgentRunTransitionEvent.FAIL),
    ],
)
def test_agent_run_rejects_state_skips(
    source: AgentRunStatus,
    event: AgentRunTransitionEvent,
) -> None:
    with pytest.raises(InvalidStateTransition):
        transition_agent_run(source, event)


@pytest.mark.parametrize(
    ("source", "event", "expected"),
    [
        (
            ApprovalRequestStatus.PENDING,
            ApprovalRequestEvent.APPROVE,
            ApprovalRequestStatus.APPROVED,
        ),
        (
            ApprovalRequestStatus.PENDING,
            ApprovalRequestEvent.REJECT,
            ApprovalRequestStatus.REJECTED,
        ),
        (
            ApprovalRequestStatus.PENDING,
            ApprovalRequestEvent.EXPIRE,
            ApprovalRequestStatus.EXPIRED,
        ),
        (
            ApprovalRequestStatus.PENDING,
            ApprovalRequestEvent.REVOKE,
            ApprovalRequestStatus.REVOKED,
        ),
        (
            ApprovalRequestStatus.APPROVED,
            ApprovalRequestEvent.CONSUME,
            ApprovalRequestStatus.CONSUMED,
        ),
        (
            ApprovalRequestStatus.APPROVED,
            ApprovalRequestEvent.EXPIRE,
            ApprovalRequestStatus.EXPIRED,
        ),
        (
            ApprovalRequestStatus.APPROVED,
            ApprovalRequestEvent.REVOKE,
            ApprovalRequestStatus.REVOKED,
        ),
    ],
)
def test_approval_request_transitions(
    source: ApprovalRequestStatus,
    event: ApprovalRequestEvent,
    expected: ApprovalRequestStatus,
) -> None:
    assert transition_approval_request(source, event) is expected


def test_approval_terminal_state_cannot_be_overwritten() -> None:
    with pytest.raises(InvalidStateTransition):
        transition_approval_request(ApprovalRequestStatus.REJECTED, ApprovalRequestEvent.APPROVE)


@pytest.mark.parametrize(
    ("source", "event", "expected"),
    [
        (
            ToolExecutionStatus.PENDING,
            ToolExecutionEvent.BEGIN,
            ToolExecutionStatus.RUNNING,
        ),
        (
            ToolExecutionStatus.PENDING,
            ToolExecutionEvent.DENY,
            ToolExecutionStatus.DENIED,
        ),
        (
            ToolExecutionStatus.RUNNING,
            ToolExecutionEvent.SUCCEED,
            ToolExecutionStatus.SUCCEEDED,
        ),
        (
            ToolExecutionStatus.RUNNING,
            ToolExecutionEvent.FAIL,
            ToolExecutionStatus.FAILED,
        ),
        (
            ToolExecutionStatus.RUNNING,
            ToolExecutionEvent.DENY,
            ToolExecutionStatus.DENIED,
        ),
    ],
)
def test_tool_execution_transitions(
    source: ToolExecutionStatus,
    event: ToolExecutionEvent,
    expected: ToolExecutionStatus,
) -> None:
    assert transition_tool_execution(source, event) is expected


@pytest.mark.parametrize(
    ("source", "event", "expected"),
    [
        (
            AgentArtifactStatus.WRITING,
            AgentArtifactEvent.MARK_DRAFT_READY,
            AgentArtifactStatus.DRAFT_READY,
        ),
        (
            AgentArtifactStatus.WRITING,
            AgentArtifactEvent.FAIL,
            AgentArtifactStatus.FAILED,
        ),
        (
            AgentArtifactStatus.DRAFT_READY,
            AgentArtifactEvent.PUBLISH,
            AgentArtifactStatus.PUBLISHED,
        ),
        (
            AgentArtifactStatus.DRAFT_READY,
            AgentArtifactEvent.REVOKE,
            AgentArtifactStatus.REVOKED,
        ),
        (
            AgentArtifactStatus.PUBLISHED,
            AgentArtifactEvent.REVOKE,
            AgentArtifactStatus.REVOKED,
        ),
    ],
)
def test_agent_artifact_transitions(
    source: AgentArtifactStatus,
    event: AgentArtifactEvent,
    expected: AgentArtifactStatus,
) -> None:
    assert transition_agent_artifact(source, event) is expected
