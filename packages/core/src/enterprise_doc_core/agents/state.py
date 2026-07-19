from __future__ import annotations

from enum import StrEnum

from enterprise_doc_core.agents.models import (
    AgentArtifactStatus,
    AgentRunStatus,
    ApprovalRequestStatus,
    ToolExecutionStatus,
)


class AgentRunTransitionEvent(StrEnum):
    START = "start"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    RESUME = "resume"
    SUCCEED = "succeed"
    REFUSE = "refuse"
    FAIL = "fail"
    CANCEL = "cancel"
    REJECT = "reject"
    EXPIRE = "expire"


class ApprovalRequestEvent(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EXPIRE = "expire"
    REVOKE = "revoke"
    CONSUME = "consume"


class ToolExecutionEvent(StrEnum):
    BEGIN = "begin"
    SUCCEED = "succeed"
    FAIL = "fail"
    DENY = "deny"


class AgentArtifactEvent(StrEnum):
    MARK_DRAFT_READY = "mark_draft_ready"
    PUBLISH = "publish"
    FAIL = "fail"
    REVOKE = "revoke"


class InvalidStateTransition(ValueError):
    def __init__(self, entity: str, current: StrEnum, event: StrEnum) -> None:
        self.entity = entity
        self.current = current
        self.event = event
        super().__init__(f"invalid {entity} transition from {current.value} via {event.value}")


AGENT_RUN_TRANSITIONS = {
    (AgentRunStatus.PENDING, AgentRunTransitionEvent.START): AgentRunStatus.RUNNING,
    (
        AgentRunStatus.RUNNING,
        AgentRunTransitionEvent.WAIT_FOR_APPROVAL,
    ): AgentRunStatus.WAITING_APPROVAL,
    (AgentRunStatus.WAITING_APPROVAL, AgentRunTransitionEvent.RESUME): AgentRunStatus.RUNNING,
    (AgentRunStatus.RUNNING, AgentRunTransitionEvent.SUCCEED): AgentRunStatus.SUCCEEDED,
    (AgentRunStatus.RUNNING, AgentRunTransitionEvent.REFUSE): AgentRunStatus.REFUSED,
    (AgentRunStatus.RUNNING, AgentRunTransitionEvent.FAIL): AgentRunStatus.FAILED,
    (AgentRunStatus.PENDING, AgentRunTransitionEvent.FAIL): AgentRunStatus.FAILED,
    (
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunTransitionEvent.FAIL,
    ): AgentRunStatus.FAILED,
    (AgentRunStatus.PENDING, AgentRunTransitionEvent.CANCEL): AgentRunStatus.CANCELLED,
    (AgentRunStatus.RUNNING, AgentRunTransitionEvent.CANCEL): AgentRunStatus.CANCELLED,
    (
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunTransitionEvent.CANCEL,
    ): AgentRunStatus.CANCELLED,
    (
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunTransitionEvent.REJECT,
    ): AgentRunStatus.REJECTED,
    (
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunTransitionEvent.EXPIRE,
    ): AgentRunStatus.EXPIRED,
}

APPROVAL_REQUEST_TRANSITIONS = {
    (ApprovalRequestStatus.PENDING, ApprovalRequestEvent.APPROVE): ApprovalRequestStatus.APPROVED,
    (ApprovalRequestStatus.PENDING, ApprovalRequestEvent.REJECT): ApprovalRequestStatus.REJECTED,
    (ApprovalRequestStatus.PENDING, ApprovalRequestEvent.EXPIRE): ApprovalRequestStatus.EXPIRED,
    (ApprovalRequestStatus.PENDING, ApprovalRequestEvent.REVOKE): ApprovalRequestStatus.REVOKED,
    (
        ApprovalRequestStatus.APPROVED,
        ApprovalRequestEvent.CONSUME,
    ): ApprovalRequestStatus.CONSUMED,
    (ApprovalRequestStatus.APPROVED, ApprovalRequestEvent.EXPIRE): ApprovalRequestStatus.EXPIRED,
    (ApprovalRequestStatus.APPROVED, ApprovalRequestEvent.REVOKE): ApprovalRequestStatus.REVOKED,
}

TOOL_EXECUTION_TRANSITIONS = {
    (ToolExecutionStatus.PENDING, ToolExecutionEvent.BEGIN): ToolExecutionStatus.RUNNING,
    (ToolExecutionStatus.PENDING, ToolExecutionEvent.DENY): ToolExecutionStatus.DENIED,
    (ToolExecutionStatus.RUNNING, ToolExecutionEvent.SUCCEED): ToolExecutionStatus.SUCCEEDED,
    (ToolExecutionStatus.RUNNING, ToolExecutionEvent.FAIL): ToolExecutionStatus.FAILED,
    (ToolExecutionStatus.RUNNING, ToolExecutionEvent.DENY): ToolExecutionStatus.DENIED,
}

AGENT_ARTIFACT_TRANSITIONS = {
    (
        AgentArtifactStatus.WRITING,
        AgentArtifactEvent.MARK_DRAFT_READY,
    ): AgentArtifactStatus.DRAFT_READY,
    (AgentArtifactStatus.WRITING, AgentArtifactEvent.FAIL): AgentArtifactStatus.FAILED,
    (AgentArtifactStatus.DRAFT_READY, AgentArtifactEvent.PUBLISH): AgentArtifactStatus.PUBLISHED,
    (AgentArtifactStatus.DRAFT_READY, AgentArtifactEvent.REVOKE): AgentArtifactStatus.REVOKED,
    (AgentArtifactStatus.PUBLISHED, AgentArtifactEvent.REVOKE): AgentArtifactStatus.REVOKED,
}

TERMINAL_AGENT_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.REFUSED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.EXPIRED,
    }
)


def _transition[StatusT: StrEnum, EventT: StrEnum](
    entity: str,
    current: StatusT,
    event: EventT,
    transitions: dict[tuple[StatusT, EventT], StatusT],
) -> StatusT:
    try:
        return transitions[(current, event)]
    except KeyError as error:
        raise InvalidStateTransition(entity, current, event) from error


def transition_agent_run(
    current: AgentRunStatus,
    event: AgentRunTransitionEvent,
) -> AgentRunStatus:
    return _transition("agent run", current, event, AGENT_RUN_TRANSITIONS)


def is_agent_run_terminal(status: AgentRunStatus) -> bool:
    return status in TERMINAL_AGENT_RUN_STATUSES


def transition_approval_request(
    current: ApprovalRequestStatus,
    event: ApprovalRequestEvent,
) -> ApprovalRequestStatus:
    return _transition("approval request", current, event, APPROVAL_REQUEST_TRANSITIONS)


def transition_tool_execution(
    current: ToolExecutionStatus,
    event: ToolExecutionEvent,
) -> ToolExecutionStatus:
    return _transition("tool execution", current, event, TOOL_EXECUTION_TRANSITIONS)


def transition_agent_artifact(
    current: AgentArtifactStatus,
    event: AgentArtifactEvent,
) -> AgentArtifactStatus:
    return _transition("agent artifact", current, event, AGENT_ARTIFACT_TRANSITIONS)
