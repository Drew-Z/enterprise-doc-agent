from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from enterprise_doc_core.agents import (
    BehaviorVersions,
    DeterministicGroundedGateway,
    GroundedAnswer,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelRequest,
    ModelIdentity,
    ModelRefusalOutput,
)
from enterprise_doc_core.agents.graph import (
    AgentGraphVersionMismatch,
    GraphApprovalDecision,
    GraphApprovalResult,
    GraphDraftResult,
    GraphRetrievalResult,
    GraphRiskResult,
    build_agent_graph,
    graph_config,
    initial_graph_state,
)
from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.documents.retrieval import RefusalReason

TENANT_ID = uuid4()
ACTOR_ID = uuid4()
RUN_ID = uuid4()
VERSION_ID = uuid4()
CHUNK_ID = uuid4()
GENERATION_ID = uuid4()


@dataclass
class FakeGraphBackend:
    publish_requested: bool = False
    accepted: bool = True
    high_risk: bool = False
    evidence: GroundedEvidence | None = None
    calls: list[str] = field(default_factory=list)
    outputs: dict[str, GroundedModelOutput] = field(default_factory=dict)
    answers: dict[str, GroundedAnswer] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.evidence = self.evidence or GroundedEvidence(
            chunk_id=CHUNK_ID,
            tenant_id=TENANT_ID,
            document_version_id=VERSION_ID,
            generation_id=GENERATION_ID,
            text="Payment is due within 30 days.",
            rank=1,
            score=0.95,
            start_offset=0,
            end_offset=32,
        )

    async def load_run(self, state: dict[str, Any]) -> None:
        self.calls.append("load_run")
        if state["graph_version"] != "m4.v2":
            raise AgentGraphVersionMismatch()

    async def authorize(self, state: dict[str, Any]) -> None:
        self.calls.append("authorize")

    async def retrieve_evidence(self, state: dict[str, Any]) -> GraphRetrievalResult:
        self.calls.append("retrieve_evidence")
        if not self.accepted:
            return GraphRetrievalResult(
                accepted=False,
                evidence_ids=(),
                refusal_reason=RefusalReason.EMPTY_EVIDENCE,
            )
        assert self.evidence is not None
        return GraphRetrievalResult(True, (str(self.evidence.chunk_id),), None)

    async def build_model_request(self, state: dict[str, Any]) -> GroundedModelRequest:
        self.calls.append("build_model_request")
        assert self.evidence is not None
        return GroundedModelRequest(
            task_type=AgentRunTaskType.QUESTION_ANSWER,
            user_input="What are the payment terms?",
            evidence=[self.evidence],
            behavior_versions=BehaviorVersions(
                graph_version="m4.v2",
                prompt_version="m4.prompt.v2",
                tool_schema_version="m4.tools.v2",
            ),
        )

    async def stage_model_output(
        self,
        state: dict[str, Any],
        output: GroundedModelOutput,
    ) -> str:
        self.calls.append("stage_model_output")
        fingerprint = "a" * 64
        self.outputs[fingerprint] = output
        return fingerprint

    async def load_model_output(
        self,
        state: dict[str, Any],
        fingerprint: str,
    ) -> GroundedModelOutput:
        self.calls.append("load_model_output")
        return self.outputs[fingerprint]

    async def store_validated_answer(
        self,
        state: dict[str, Any],
        answer: GroundedAnswer,
    ) -> str:
        self.calls.append("store_validated_answer")
        fingerprint = "b" * 64
        self.answers[fingerprint] = answer
        return fingerprint

    async def create_draft(
        self,
        state: dict[str, Any],
        answer_fingerprint: str,
    ) -> GraphDraftResult:
        self.calls.append("create_draft")
        return GraphDraftResult(
            artifact_id=str(uuid4()),
            target_fingerprint="c" * 64,
        )

    async def assess_risk(self, state: dict[str, Any]) -> GraphRiskResult:
        self.calls.append("assess_risk")
        return GraphRiskResult(requires_approval=self.high_risk)

    async def create_approval(
        self,
        state: dict[str, Any],
        draft: GraphDraftResult,
    ) -> GraphApprovalResult:
        self.calls.append("create_approval")
        return GraphApprovalResult(approval_request_id=str(uuid4()))

    async def validate_approval(
        self,
        state: dict[str, Any],
        decision: GraphApprovalDecision,
    ) -> None:
        self.calls.append("validate_approval")
        if decision.approval_id != state["approval_request_id"]:
            raise ValueError("approval target mismatch")

    async def publish_artifact(self, state: dict[str, Any]) -> None:
        self.calls.append("publish_artifact")

    async def finalize(
        self,
        state: dict[str, Any],
        outcome: str,
        refusal_reason: str | None = None,
    ) -> None:
        self.calls.append(f"finalize:{outcome}")


class FixedRefusalGateway:
    async def generate(self, request: GroundedModelRequest) -> GroundedModelOutput:
        return GroundedModelOutput(
            payload=ModelRefusalOutput(
                outcome="refusal",
                task_type=request.task_type,
                refusal_reason="insufficient_evidence",
                answer_text=None,
                structured_fields=None,
                citations=[],
                risk_hint=None,
            ),
            identity=ModelIdentity(
                provider="openai_compatible",
                model_name="test-model",
                model_version="2026-08",
            ),
        )


def _state(*, publish_requested: bool = False) -> dict[str, Any]:
    return initial_graph_state(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        document_version_id=VERSION_ID,
        task_type=AgentRunTaskType.QUESTION_ANSWER,
        publish_requested=publish_requested,
        graph_version="m4.v2",
    )


def test_initial_graph_state_is_json_only_and_uses_stable_thread_config() -> None:
    state = _state()

    encoded = json.dumps(state, sort_keys=True)
    assert "Payment" not in encoded
    assert set(state) == {
        "run_id",
        "tenant_id",
        "actor_id",
        "document_version_id",
        "task_type",
        "publish_requested",
        "graph_version",
        "evidence_ids",
        "approval_required",
    }
    assert state["evidence_ids"] == []
    assert state["approval_required"] is False
    config = graph_config(RUN_ID)
    assert config["configurable"]["thread_id"] == str(RUN_ID)
    assert config["configurable"]["checkpoint_ns"] == ""


@pytest.mark.asyncio
async def test_graph_refuses_without_calling_gateway_or_write_nodes() -> None:
    backend = FakeGraphBackend(accepted=False)
    gateway = DeterministicGroundedGateway()
    graph = build_agent_graph(backend=backend, gateway=gateway, checkpointer=InMemorySaver())

    result = await graph.ainvoke(_state(), config=graph_config(RUN_ID))

    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == RefusalReason.EMPTY_EVIDENCE.value
    assert "generate_answer" not in backend.calls
    assert "create_draft" not in backend.calls
    assert backend.calls[-1] == "finalize:refused"


@pytest.mark.asyncio
async def test_graph_routes_valid_model_refusal_away_from_artifact_nodes() -> None:
    backend = FakeGraphBackend(accepted=True)
    graph = build_agent_graph(
        backend=backend,
        gateway=FixedRefusalGateway(),
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(_state(), config=graph_config(RUN_ID))

    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == RefusalReason.INSUFFICIENT_EVIDENCE.value
    assert "store_validated_answer" not in backend.calls
    assert "create_draft" not in backend.calls
    assert "assess_risk" not in backend.calls
    assert "publish_artifact" not in backend.calls
    assert backend.calls[-1] == "finalize:refused"


@pytest.mark.asyncio
async def test_graph_generates_validated_draft_and_finalizes_success() -> None:
    backend = FakeGraphBackend()
    graph = build_agent_graph(
        backend=backend,
        gateway=DeterministicGroundedGateway(),
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(_state(), config=graph_config(RUN_ID))

    assert result["outcome"] == "succeeded"
    assert result["answer_fingerprint"] == "b" * 64
    assert result["answer_artifact_id"]
    assert "Based on the authorized evidence" not in json.dumps(result)
    assert backend.calls.count("stage_model_output") == 1
    assert backend.calls.count("store_validated_answer") == 1
    assert backend.calls[-1] == "finalize:succeeded"


@pytest.mark.asyncio
async def test_graph_interrupts_and_resumes_approval_on_same_thread() -> None:
    backend = FakeGraphBackend(high_risk=True)
    saver = InMemorySaver()
    graph = build_agent_graph(
        backend=backend,
        gateway=DeterministicGroundedGateway(),
        checkpointer=saver,
    )
    config = graph_config(RUN_ID)

    paused = await graph.ainvoke(_state(publish_requested=True), config=config)

    assert "__interrupt__" in paused
    assert paused["approval_request_id"]
    approval_id = paused["approval_request_id"]
    assert "publish_artifact" not in backend.calls

    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_id": approval_id,
                "decision": "approved",
                "decision_fingerprint": "d" * 64,
            }
        ),
        config=config,
    )

    assert resumed["outcome"] == "succeeded"
    assert "publish_artifact" in backend.calls
    assert backend.calls.count("create_approval") == 1
    assert backend.calls[-1] == "finalize:succeeded"


@pytest.mark.asyncio
async def test_graph_version_mismatch_is_stable_and_before_side_effects() -> None:
    backend = FakeGraphBackend()
    graph = build_agent_graph(
        backend=backend,
        gateway=DeterministicGroundedGateway(),
        checkpointer=InMemorySaver(),
        graph_version="m4.v3",
    )

    with pytest.raises(AgentGraphVersionMismatch):
        await graph.ainvoke(_state(), config=graph_config(RUN_ID))

    # Compatibility is checked before any backend side effect.
    assert backend.calls == []
