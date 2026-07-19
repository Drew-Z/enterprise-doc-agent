from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Protocol, TypedDict, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_doc_core.agents.gateway import ChatModelGateway
from enterprise_doc_core.agents.grounding import validate_grounded_output
from enterprise_doc_core.agents.schemas import (
    GroundedAnswer,
    GroundedModelOutput,
    GroundedModelRequest,
)
from enterprise_doc_core.documents.retrieval import RefusalReason

AGENT_GRAPH_VERSION = "m4.v1"
_HASH_PATTERN = r"^[0-9a-f]{64}$"


class AgentGraphState(TypedDict):
    run_id: str
    tenant_id: str
    actor_id: str
    document_version_id: str
    task_type: str
    publish_requested: bool
    graph_version: str
    evidence_ids: NotRequired[list[str]]
    model_output_fingerprint: NotRequired[str]
    answer_fingerprint: NotRequired[str]
    answer_artifact_id: NotRequired[str]
    artifact_fingerprint: NotRequired[str]
    approval_request_id: NotRequired[str]
    approval_decision: NotRequired[str]
    approval_required: NotRequired[bool]
    outcome: NotRequired[str]
    refusal_reason: NotRequired[str]


class AgentGraphStateContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str
    tenant_id: str
    actor_id: str
    document_version_id: str
    task_type: str
    publish_requested: bool
    graph_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    model_output_fingerprint: str | None = Field(default=None, pattern=_HASH_PATTERN)
    answer_fingerprint: str | None = Field(default=None, pattern=_HASH_PATTERN)
    answer_artifact_id: str | None = None
    artifact_fingerprint: str | None = Field(default=None, pattern=_HASH_PATTERN)
    approval_request_id: str | None = None
    approval_decision: Literal["approved", "rejected", "expired"] | None = None
    approval_required: bool = False
    outcome: str | None = None
    refusal_reason: str | None = None

    @field_validator("run_id", "tenant_id", "actor_id", "document_version_id")
    @classmethod
    def validate_uuid_string(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as error:
            raise ValueError("graph identifiers must be UUID strings") from error
        return value

    @field_validator("task_type", "graph_version")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("graph state strings must not be blank")
        return value


class AgentGraphError(RuntimeError):
    code = "agent_graph_error"
    retryable = False


class AgentGraphVersionMismatch(AgentGraphError):
    code = "agent_graph_version_mismatch"


@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    accepted: bool
    evidence_ids: tuple[str, ...]
    refusal_reason: RefusalReason | None = None


@dataclass(frozen=True, slots=True)
class GraphDraftResult:
    artifact_id: str
    target_fingerprint: str


@dataclass(frozen=True, slots=True)
class GraphRiskResult:
    requires_approval: bool


@dataclass(frozen=True, slots=True)
class GraphApprovalResult:
    approval_request_id: str


class GraphApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    approval_id: str
    decision: Literal["approved", "rejected", "expired"]
    decision_fingerprint: str = Field(pattern=_HASH_PATTERN)

    @field_validator("approval_id")
    @classmethod
    def validate_approval_id(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as error:
            raise ValueError("approval_id must be a UUID string") from error
        return value


class AgentGraphBackend(Protocol):
    """Re-entrant boundary for DB/tool side effects outside checkpoint state."""

    async def load_run(self, state: AgentGraphState) -> None: ...

    async def authorize(self, state: AgentGraphState) -> None: ...

    async def retrieve_evidence(self, state: AgentGraphState) -> GraphRetrievalResult: ...

    async def build_model_request(self, state: AgentGraphState) -> GroundedModelRequest: ...

    async def stage_model_output(
        self,
        state: AgentGraphState,
        output: GroundedModelOutput,
    ) -> str: ...

    async def load_model_output(
        self,
        state: AgentGraphState,
        fingerprint: str,
    ) -> GroundedModelOutput: ...

    async def store_validated_answer(
        self,
        state: AgentGraphState,
        answer: GroundedAnswer,
    ) -> str: ...

    async def create_draft(
        self,
        state: AgentGraphState,
        answer_fingerprint: str,
    ) -> GraphDraftResult: ...

    async def assess_risk(self, state: AgentGraphState) -> GraphRiskResult: ...

    async def create_approval(
        self,
        state: AgentGraphState,
        draft: GraphDraftResult,
    ) -> GraphApprovalResult: ...

    async def mark_waiting_for_approval(self) -> None: ...

    async def validate_approval(
        self,
        state: AgentGraphState,
        decision: GraphApprovalDecision,
    ) -> None: ...

    async def publish_artifact(self, state: AgentGraphState) -> None: ...

    async def finalize(
        self,
        state: AgentGraphState,
        outcome: str,
        refusal_reason: str | None = None,
    ) -> None: ...


def initial_graph_state(
    *,
    run_id: UUID | str,
    tenant_id: UUID | str,
    actor_id: UUID | str,
    document_version_id: UUID | str,
    task_type: str,
    publish_requested: bool,
    graph_version: str = AGENT_GRAPH_VERSION,
) -> AgentGraphState:
    state = AgentGraphStateContract(
        run_id=str(run_id),
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        document_version_id=str(document_version_id),
        task_type=task_type,
        publish_requested=publish_requested,
        graph_version=graph_version,
    )
    return cast(AgentGraphState, state.model_dump(exclude_none=True))


def validate_graph_state(state: Mapping[str, Any]) -> AgentGraphStateContract:
    return AgentGraphStateContract.model_validate(dict(state))


def graph_config(run_id: UUID | str) -> dict[str, Any]:
    """Return the stable top-level LangGraph thread configuration.

    LangGraph 1.2 treats ``checkpoint_ns`` as a subgraph namespace. The root graph
    therefore uses the empty namespace; graph compatibility is enforced in state and
    by the backend rather than pretending a version is a subgraph name.
    """
    return {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}}


def build_agent_graph(
    *,
    backend: AgentGraphBackend,
    gateway: ChatModelGateway,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    graph_version: str = AGENT_GRAPH_VERSION,
) -> CompiledStateGraph[AgentGraphState, None, AgentGraphState, AgentGraphState]:
    graph = StateGraph(AgentGraphState)

    async def load_run(state: AgentGraphState) -> dict[str, Any]:
        _require_version(state, graph_version)
        await backend.load_run(state)
        return {}

    async def authorize(state: AgentGraphState) -> dict[str, Any]:
        validate_graph_state(state)
        await backend.authorize(state)
        return {}

    async def retrieve_evidence(state: AgentGraphState) -> dict[str, Any]:
        validate_graph_state(state)
        result = await backend.retrieve_evidence(state)
        if not result.accepted:
            reason = result.refusal_reason or RefusalReason.EMPTY_EVIDENCE
            return {
                "evidence_ids": list(result.evidence_ids),
                "outcome": "refused",
                "refusal_reason": reason.value,
            }
        return {"evidence_ids": list(result.evidence_ids)}

    async def generate_answer(state: AgentGraphState) -> dict[str, Any]:
        validate_graph_state(state)
        request = await backend.build_model_request(state)
        output = await gateway.generate(request)
        fingerprint = await backend.stage_model_output(state, output)
        return {"model_output_fingerprint": fingerprint}

    async def validate_answer(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        fingerprint = contract.model_output_fingerprint
        if fingerprint is None:
            raise AgentGraphError("model output fingerprint is missing")
        request = await backend.build_model_request(state)
        output = await backend.load_model_output(state, fingerprint)
        answer = validate_grounded_output(
            output,
            request=request,
            tenant_id=UUID(contract.tenant_id),
            document_version_id=UUID(contract.document_version_id),
        )
        answer_fingerprint = await backend.store_validated_answer(state, answer)
        return {"answer_fingerprint": answer_fingerprint}

    async def create_draft(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        if contract.answer_fingerprint is None:
            raise AgentGraphError("validated answer fingerprint is missing")
        draft = await backend.create_draft(state, contract.answer_fingerprint)
        return {
            "answer_artifact_id": draft.artifact_id,
            "artifact_fingerprint": draft.target_fingerprint,
        }

    async def assess_risk(state: AgentGraphState) -> dict[str, Any]:
        validate_graph_state(state)
        risk = await backend.assess_risk(state)
        return {"approval_required": risk.requires_approval}

    async def create_approval(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        if contract.answer_artifact_id is None or contract.artifact_fingerprint is None:
            raise AgentGraphError("draft target is missing")
        approval = await backend.create_approval(
            state,
            GraphDraftResult(
                artifact_id=contract.answer_artifact_id,
                target_fingerprint=contract.artifact_fingerprint,
            ),
        )
        return {"approval_request_id": approval.approval_request_id, "outcome": "waiting_approval"}

    async def approval_interrupt(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        approval_id = contract.approval_request_id
        if approval_id is None:
            raise AgentGraphError("approval request is missing")
        decision_value = interrupt(
            {
                "approval_id": approval_id,
                "operation": "publish_artifact",
                "artifact_id": contract.answer_artifact_id,
                "target_fingerprint": contract.artifact_fingerprint,
            }
        )
        decision = GraphApprovalDecision.model_validate(decision_value)
        await backend.validate_approval(state, decision)
        return {"approval_decision": decision.decision}

    async def publish_artifact(state: AgentGraphState) -> dict[str, Any]:
        validate_graph_state(state)
        await backend.publish_artifact(state)
        return {}

    async def finalize_refused(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        await backend.finalize(state, "refused", contract.refusal_reason)
        return {"outcome": "refused"}

    async def finalize_rejected(state: AgentGraphState) -> dict[str, Any]:
        contract = validate_graph_state(state)
        outcome = contract.approval_decision or "rejected"
        await backend.finalize(state, outcome, None)
        return {"outcome": outcome}

    async def finalize_success(state: AgentGraphState) -> dict[str, Any]:
        await backend.finalize(state, "succeeded", None)
        return {"outcome": "succeeded"}

    graph.add_node("load_run", load_run)
    graph.add_node("authorize", authorize)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("generate_answer", generate_answer)
    graph.add_node("validate_answer", validate_answer)
    graph.add_node("create_draft", create_draft)
    graph.add_node("assess_risk", assess_risk)
    graph.add_node("create_approval", create_approval)
    graph.add_node("approval_interrupt", approval_interrupt)
    graph.add_node("publish_artifact", publish_artifact)
    graph.add_node("finalize_refused", finalize_refused)
    graph.add_node("finalize_rejected", finalize_rejected)
    graph.add_node("finalize_success", finalize_success)

    graph.add_edge(START, "load_run")
    graph.add_edge("load_run", "authorize")
    graph.add_edge("authorize", "retrieve_evidence")
    graph.add_conditional_edges(
        "retrieve_evidence",
        lambda state: "refused" if state.get("outcome") == "refused" else "accepted",
        {"refused": "finalize_refused", "accepted": "generate_answer"},
    )
    graph.add_edge("generate_answer", "validate_answer")
    graph.add_edge("validate_answer", "create_draft")
    graph.add_edge("create_draft", "assess_risk")
    graph.add_conditional_edges(
        "assess_risk",
        lambda state: "approval" if state.get("approval_required") else "success",
        {"approval": "create_approval", "success": "finalize_success"},
    )
    graph.add_edge("create_approval", "approval_interrupt")
    graph.add_conditional_edges(
        "approval_interrupt",
        lambda state: "publish" if state.get("approval_decision") == "approved" else "rejected",
        {"publish": "publish_artifact", "rejected": "finalize_rejected"},
    )
    graph.add_edge("publish_artifact", "finalize_success")
    graph.add_edge("finalize_refused", END)
    graph.add_edge("finalize_rejected", END)
    graph.add_edge("finalize_success", END)

    return graph.compile(checkpointer=checkpointer)


def _require_version(state: Mapping[str, Any], graph_version: str) -> None:
    try:
        contract = validate_graph_state(state)
    except ValueError as error:
        raise AgentGraphVersionMismatch() from error
    if contract.graph_version != graph_version:
        raise AgentGraphVersionMismatch()


__all__ = [
    "AGENT_GRAPH_VERSION",
    "AgentGraphBackend",
    "AgentGraphError",
    "AgentGraphState",
    "AgentGraphStateContract",
    "AgentGraphVersionMismatch",
    "GraphApprovalDecision",
    "GraphApprovalResult",
    "GraphDraftResult",
    "GraphRetrievalResult",
    "GraphRiskResult",
    "build_agent_graph",
    "graph_config",
    "initial_graph_state",
    "validate_graph_state",
]
