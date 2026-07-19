from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.types import Command

from enterprise_doc_core.agents import (
    BehaviorVersions,
    CheckpointRuntime,
    DeterministicGroundedGateway,
    GraphApprovalDecision,
    GraphApprovalResult,
    GraphDraftResult,
    GraphRetrievalResult,
    GraphRiskResult,
    GroundedAnswer,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelRequest,
    build_agent_graph,
    graph_config,
    initial_graph_state,
)
from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.config import FoundationSettings

pytestmark = pytest.mark.integration


class InjectedCrash(RuntimeError):
    pass


@dataclass
class RecoveryStore:
    tenant_id: UUID
    document_version_id: UUID
    evidence_id: UUID = field(default_factory=uuid4)
    generation_id: UUID = field(default_factory=uuid4)
    evidence_frozen: bool = False
    model_outputs: dict[str, GroundedModelOutput] = field(default_factory=dict)
    answers: dict[str, GroundedAnswer] = field(default_factory=dict)
    draft: GraphDraftResult | None = None
    approval: GraphApprovalResult | None = None
    published: bool = False
    terminal_outcome: str | None = None
    effective_writes: Counter[str] = field(default_factory=Counter)


@dataclass
class CrashPlan:
    target: str | None
    fired: bool = False

    def after(self, node: str) -> None:
        if self.target == node and not self.fired:
            self.fired = True
            raise InjectedCrash(node)


class CountingGateway:
    def __init__(self, calls: list[int]) -> None:
        self.calls = calls
        self.delegate = DeterministicGroundedGateway()

    async def generate(self, request: GroundedModelRequest) -> GroundedModelOutput:
        self.calls[0] += 1
        return await self.delegate.generate(request)


class RecoveryBackend:
    def __init__(self, store: RecoveryStore, plan: CrashPlan) -> None:
        self.store = store
        self.plan = plan

    async def load_run(self, state: dict[str, Any]) -> None:
        self.plan.after("load")

    async def authorize(self, state: dict[str, Any]) -> None:
        self.plan.after("authorize")

    async def retrieve_evidence(self, state: dict[str, Any]) -> GraphRetrievalResult:
        if not self.store.evidence_frozen:
            self.store.evidence_frozen = True
            self.store.effective_writes["retrieval"] += 1
        self.plan.after("retrieve")
        return GraphRetrievalResult(True, (str(self.store.evidence_id),), None)

    async def build_model_request(self, state: dict[str, Any]) -> GroundedModelRequest:
        evidence = GroundedEvidence(
            chunk_id=self.store.evidence_id,
            tenant_id=self.store.tenant_id,
            document_version_id=self.store.document_version_id,
            generation_id=self.store.generation_id,
            text="Payment is due within 30 days after acceptance.",
            rank=1,
            score=0.95,
            page_number=1,
            heading="Payment",
            source_filename="contract.txt",
            start_offset=0,
            end_offset=48,
        )
        return GroundedModelRequest(
            task_type=AgentRunTaskType.QUESTION_ANSWER,
            user_input="What are the payment terms?",
            evidence=[evidence],
            behavior_versions=BehaviorVersions(
                graph_version="m4.v1",
                prompt_version="m4.prompt.v1",
                tool_schema_version="m4.tools.v1",
            ),
        )

    async def stage_model_output(
        self,
        state: dict[str, Any],
        output: GroundedModelOutput,
    ) -> str:
        fingerprint = _fingerprint(output)
        if fingerprint not in self.store.model_outputs:
            self.store.model_outputs[fingerprint] = output
            self.store.effective_writes["model_output"] += 1
        self.plan.after("generate")
        return fingerprint

    async def load_model_output(
        self,
        state: dict[str, Any],
        fingerprint: str,
    ) -> GroundedModelOutput:
        return self.store.model_outputs[fingerprint]

    async def store_validated_answer(
        self,
        state: dict[str, Any],
        answer: GroundedAnswer,
    ) -> str:
        fingerprint = _fingerprint(answer)
        if fingerprint not in self.store.answers:
            self.store.answers[fingerprint] = answer
            self.store.effective_writes["answer"] += 1
        self.plan.after("validate")
        return fingerprint

    async def create_draft(
        self,
        state: dict[str, Any],
        answer_fingerprint: str,
    ) -> GraphDraftResult:
        if self.store.draft is None:
            self.store.draft = GraphDraftResult(
                artifact_id=str(uuid4()),
                target_fingerprint="c" * 64,
            )
            self.store.effective_writes["draft"] += 1
        self.plan.after("draft")
        return self.store.draft

    async def assess_risk(self, state: dict[str, Any]) -> GraphRiskResult:
        self.plan.after("risk")
        return GraphRiskResult(requires_approval=bool(state.get("publish_requested")))

    async def create_approval(
        self,
        state: dict[str, Any],
        draft: GraphDraftResult,
    ) -> GraphApprovalResult:
        if self.store.approval is None:
            self.store.approval = GraphApprovalResult(approval_request_id=str(uuid4()))
            self.store.effective_writes["approval"] += 1
        self.plan.after("approval")
        return self.store.approval

    async def validate_approval(
        self,
        state: dict[str, Any],
        decision: GraphApprovalDecision,
    ) -> None:
        assert self.store.approval is not None
        assert decision.approval_id == self.store.approval.approval_request_id

    async def publish_artifact(self, state: dict[str, Any]) -> None:
        if not self.store.published:
            self.store.published = True
            self.store.effective_writes["publish"] += 1
        self.plan.after("publish")

    async def finalize(
        self,
        state: dict[str, Any],
        outcome: str,
        refusal_reason: str | None = None,
    ) -> None:
        if self.store.terminal_outcome is None:
            self.store.terminal_outcome = outcome
            self.store.effective_writes["terminal"] += 1
        self.plan.after("finalize")


def _fingerprint(value: Any) -> str:
    if isinstance(value, GroundedModelOutput):
        payload = {
            "payload": value.payload.model_dump(mode="json"),
            "identity": value.identity.model_dump(mode="json"),
            "repaired": value.repaired,
        }
    elif isinstance(value, GroundedAnswer):
        payload = {
            "task_type": value.task_type.value,
            "answer_text": value.answer_text,
            "structured_fields": value.structured_fields,
            "citations": [
                {
                    "chunk_id": str(citation.chunk_id),
                    "document_version_id": str(citation.document_version_id),
                    "excerpt": citation.excerpt,
                }
                for citation in value.citations
            ],
            "risk_hint": value.risk_hint.value if value.risk_hint else None,
            "identity": value.identity.model_dump(mode="json"),
            "repaired": value.repaired,
        }
    else:
        raise TypeError(type(value).__name__)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _state(store: RecoveryStore, *, publish_requested: bool) -> dict[str, Any]:
    return initial_graph_state(
        run_id=uuid4(),
        tenant_id=store.tenant_id,
        actor_id=uuid4(),
        document_version_id=store.document_version_id,
        task_type=AgentRunTaskType.QUESTION_ANSWER,
        publish_requested=publish_requested,
        graph_version="m4.v1",
    )


async def _open_checkpointer() -> tuple[CheckpointRuntime, Any]:
    runtime = CheckpointRuntime(FoundationSettings())
    return runtime, await runtime.open()


@pytest.mark.asyncio
async def test_graph_recovery_at_each_non_publication_node_boundary() -> None:
    runtime, checkpointer = await _open_checkpointer()
    try:
        for crash_node in (
            "load",
            "authorize",
            "retrieve",
            "generate",
            "validate",
            "draft",
            "risk",
            "finalize",
        ):
            store = RecoveryStore(tenant_id=uuid4(), document_version_id=uuid4())
            initial = _state(store, publish_requested=False)
            config = graph_config(initial["run_id"])
            calls = [0]
            first = build_agent_graph(
                backend=RecoveryBackend(store, CrashPlan(crash_node)),
                gateway=CountingGateway(calls),
                checkpointer=checkpointer,
            )
            with pytest.raises(InjectedCrash, match=crash_node):
                await first.ainvoke(initial, config=config)

            resumed = build_agent_graph(
                backend=RecoveryBackend(store, CrashPlan(None)),
                gateway=CountingGateway(calls),
                checkpointer=checkpointer,
            )
            result = await resumed.ainvoke(None, config=config)

            assert result["outcome"] == "succeeded"
            assert store.effective_writes == Counter(
                {
                    "retrieval": 1,
                    "model_output": 1,
                    "answer": 1,
                    "draft": 1,
                    "terminal": 1,
                }
            )
            assert calls[0] >= 1
            await checkpointer.adelete_thread(initial["run_id"])
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_graph_recovery_approval_and_publish_boundaries() -> None:
    runtime, checkpointer = await _open_checkpointer()
    try:
        for crash_node in ("approval", "publish"):
            store = RecoveryStore(tenant_id=uuid4(), document_version_id=uuid4())
            initial = _state(store, publish_requested=True)
            config = graph_config(initial["run_id"])
            calls = [0]
            first = build_agent_graph(
                backend=RecoveryBackend(store, CrashPlan(crash_node)),
                gateway=CountingGateway(calls),
                checkpointer=checkpointer,
            )

            if crash_node == "approval":
                with pytest.raises(InjectedCrash, match="approval"):
                    await first.ainvoke(initial, config=config)
                recovered = build_agent_graph(
                    backend=RecoveryBackend(store, CrashPlan(None)),
                    gateway=CountingGateway(calls),
                    checkpointer=checkpointer,
                )
                paused = await recovered.ainvoke(None, config=config)
            else:
                paused = await first.ainvoke(initial, config=config)

            assert "__interrupt__" in paused
            assert store.approval is not None
            approval = GraphApprovalDecision(
                approval_id=store.approval.approval_request_id,
                decision="approved",
                decision_fingerprint="d" * 64,
            )
            if crash_node == "publish":
                with pytest.raises(InjectedCrash, match="publish"):
                    await first.ainvoke(Command(resume=approval.model_dump()), config=config)
                recovered = build_agent_graph(
                    backend=RecoveryBackend(store, CrashPlan(None)),
                    gateway=CountingGateway(calls),
                    checkpointer=checkpointer,
                )
                result = await recovered.ainvoke(None, config=config)
            else:
                result = await recovered.ainvoke(
                    Command(resume=approval.model_dump()),
                    config=config,
                )

            assert result["outcome"] == "succeeded"
            assert store.effective_writes["approval"] == 1
            assert store.effective_writes["publish"] == 1
            assert store.effective_writes["terminal"] == 1
            await checkpointer.adelete_thread(initial["run_id"])
    finally:
        await runtime.close()
