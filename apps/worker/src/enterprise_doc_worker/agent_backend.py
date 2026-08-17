from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents import (
    AgentArtifact,
    AgentGraphError,
    AgentRun,
    AgentRunEvidence,
    AgentRunExecution,
    AgentRunStatus,
    AgentRunTaskType,
    ApprovalRequest,
    ApprovalRequestStatus,
    BehaviorVersions,
    ChatModelGateway,
    CreateDraftArtifactInput,
    DraftCitationInput,
    GraphApprovalDecision,
    GraphApprovalResult,
    GraphDraftResult,
    GraphRetrievalResult,
    GraphRiskResult,
    GroundedAnswer,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelRequest,
    GroundedRefusal,
    PublishArtifactInput,
    SearchDocumentInput,
    SignedExecutionContext,
    StructuredExtractionSchema,
    TargetResourceType,
    ToolCapability,
    append_agent_run_event,
    artifact_target_fingerprint,
    sign_execution_context,
    validate_grounded_output,
)
from enterprise_doc_core.agents.state import AgentRunTransitionEvent, transition_agent_run
from enterprise_doc_core.config import McpSettings
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.jobs.models import Job, JobAttempt, JobAttemptStatus, JobStatus
from enterprise_doc_worker.agent_handler import AgentExecutionContext
from enterprise_doc_worker.mcp_client import McpClient

Clock = Callable[[], datetime]


class DurableAgentGraphBackend:
    """Core-backed graph side-effect boundary used by one Worker segment."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        context: AgentExecutionContext,
        gateway: ChatModelGateway,
        mcp_client: McpClient,
        mcp_settings: McpSettings,
        clock: Clock | None = None,
        approval_ttl_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.context = context
        self.gateway = gateway
        self.mcp_client = mcp_client
        self.mcp_settings = mcp_settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.approval_ttl_seconds = approval_ttl_seconds or mcp_settings.context_ttl_seconds
        self._model_outputs: dict[str, GroundedModelOutput] = {}
        self._answers: dict[str, GroundedAnswer] = {}

    async def prepare_segment(self) -> None:
        now = self.clock()
        async with self.session_factory.begin() as session:
            run = await self._load_authorized_run(session, for_update=True)
            if self.context.execution_kind == "initial":
                if run.status == AgentRunStatus.PENDING.value:
                    run.status = transition_agent_run(
                        AgentRunStatus.PENDING,
                        AgentRunTransitionEvent.START,
                    ).value
                    run.started_at = now
                    await append_agent_run_event(
                        session,
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        event_type="run.started",
                        payload={"status": AgentRunStatus.RUNNING.value},
                        actor_id=run.actor_id,
                    )
                elif run.status not in {
                    AgentRunStatus.RUNNING.value,
                    AgentRunStatus.WAITING_APPROVAL.value,
                }:
                    raise AgentGraphError("The initial Agent segment is stale.")
                return

            if self.context.execution_kind != "resume":
                raise AgentGraphError("The Agent execution kind is unsupported.")
            if self.context.approval_decision == "approved":
                if run.status == AgentRunStatus.WAITING_APPROVAL.value:
                    run.status = transition_agent_run(
                        AgentRunStatus.WAITING_APPROVAL,
                        AgentRunTransitionEvent.RESUME,
                    ).value
                    await append_agent_run_event(
                        session,
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        event_type="run.resumed",
                        payload={"status": AgentRunStatus.RUNNING.value},
                        actor_id=run.actor_id,
                    )
                elif run.status != AgentRunStatus.RUNNING.value:
                    raise AgentGraphError("The approved Agent resume segment is stale.")
                return

            target_status = {
                "rejected": AgentRunStatus.REJECTED,
                "expired": AgentRunStatus.EXPIRED,
            }.get(self.context.approval_decision or "")
            if target_status is None:
                raise AgentGraphError("The approval decision is unsupported.")
            if run.status == target_status.value:
                return
            if run.status != AgentRunStatus.WAITING_APPROVAL.value:
                raise AgentGraphError("The approval decision is stale for this run.")
            transition_event = {
                AgentRunStatus.REJECTED: AgentRunTransitionEvent.REJECT,
                AgentRunStatus.EXPIRED: AgentRunTransitionEvent.EXPIRE,
            }[target_status]
            run.status = transition_agent_run(
                AgentRunStatus.WAITING_APPROVAL,
                transition_event,
            ).value
            run.finished_at = now
            await append_agent_run_event(
                session,
                tenant_id=run.tenant_id,
                run_id=run.id,
                event_type="run.finished",
                payload={"status": target_status.value, "refusal_reason": None},
                actor_id=run.actor_id,
            )

    async def load_run(self, state: Mapping[str, Any]) -> None:
        self._validate_state_identity(state)
        async with self.session_factory() as session:
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.id == self.context.run_id,
                    AgentRun.tenant_id == self.context.tenant_id,
                )
            )
            if run is None or run.graph_version != self.context.graph_version:
                raise AgentGraphError("Agent run is missing or has an incompatible version.")
            if run.status != AgentRunStatus.RUNNING.value:
                raise AgentGraphError("Agent run is not executable in the current state.")

    async def authorize(self, state: Mapping[str, Any]) -> None:
        self._validate_state_identity(state)
        async with self.session_factory() as session:
            await self._load_authorized_run(session)

    async def retrieve_evidence(self, state: Mapping[str, Any]) -> GraphRetrievalResult:
        self._validate_state_identity(state)
        query = await self._load_input_text()
        request = SearchDocumentInput(
            idempotency_key=self._tool_idempotency("search"),
            query=query,
        )
        result = await self.mcp_client.search_document(
            context_token=self._signed_token(ToolCapability.READ_EVIDENCE),
            request=request,
        )
        return GraphRetrievalResult(
            accepted=result.accepted,
            evidence_ids=tuple(str(candidate.chunk_id) for candidate in result.candidates),
            refusal_reason=result.refusal_reason,
        )

    async def build_model_request(self, state: Mapping[str, Any]) -> GroundedModelRequest:
        self._validate_state_identity(state)
        async with self.session_factory() as session:
            run, version, rows = await self._load_evidence_rows(session)
        evidence = [
            GroundedEvidence(
                chunk_id=evidence_row.chunk_id,
                tenant_id=evidence_row.tenant_id,
                document_version_id=evidence_row.document_version_id,
                generation_id=evidence_row.generation_id,
                text=chunk.normalized_text,
                rank=evidence_row.rank,
                score=evidence_row.rrf_score,
                page_number=chunk.page_number,
                heading=chunk.heading,
                source_filename=version.original_filename,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
            for evidence_row, chunk in rows
        ]
        extraction_schema = (
            StructuredExtractionSchema.model_validate(run.extraction_schema)
            if run.extraction_schema is not None
            else None
        )
        return GroundedModelRequest(
            task_type=AgentRunTaskType(run.task_type),
            user_input=run.input_text,
            evidence=evidence,
            extraction_schema=extraction_schema,
            behavior_versions=BehaviorVersions(
                graph_version=run.graph_version,
                prompt_version=run.prompt_version,
                tool_schema_version=run.tool_schema_version,
            ),
        )

    async def stage_model_output(
        self,
        state: Mapping[str, Any],
        output: GroundedModelOutput,
    ) -> str:
        self._validate_state_identity(state)
        fingerprint = _fingerprint_model_output(output)
        await self._record_model_telemetry(output, accumulate=False)
        self._model_outputs[fingerprint] = output
        return fingerprint

    async def load_model_output(
        self,
        state: Mapping[str, Any],
        fingerprint: str,
    ) -> GroundedModelOutput:
        cached = self._model_outputs.get(fingerprint)
        if cached is not None:
            return cached
        # A crash after the graph checkpoint may discard this process-local cache.
        # Re-generation is bounded by the gateway and is accepted only when the
        # resulting content has the same deterministic fingerprint.
        request = await self.build_model_request(state)
        output = await self.gateway.generate(request)
        if _fingerprint_model_output(output) != fingerprint:
            raise AgentGraphError("The model output changed while recovering a segment.")
        await self._record_model_telemetry(output, accumulate=True)
        self._model_outputs[fingerprint] = output
        return output

    async def _record_model_telemetry(
        self,
        output: GroundedModelOutput,
        *,
        accumulate: bool,
    ) -> None:
        async with self.session_factory.begin() as session:
            run = await self._load_authorized_run(session, for_update=True)
            run.model_provider = output.identity.provider
            run.model_name = output.identity.model_name
            run.model_version = output.identity.model_version
            run.model_revision = output.identity.model_revision
            if not accumulate and run.provider_request_count is not None:
                return

            telemetry = output.telemetry
            if not accumulate:
                run.provider_request_count = telemetry.provider_request_count
                run.provider_usage_request_count = telemetry.usage_request_count
                run.prompt_tokens = telemetry.prompt_tokens
                run.completion_tokens = telemetry.completion_tokens
                run.total_tokens = telemetry.total_tokens
                run.repair_request_count = telemetry.repair_request_count
                run.fallback_count = telemetry.fallback_count
                run.breaker_state = telemetry.breaker_state
                return

            run.provider_request_count = (
                run.provider_request_count or 0
            ) + telemetry.provider_request_count
            run.provider_usage_request_count = (
                run.provider_usage_request_count or 0
            ) + telemetry.usage_request_count
            run.prompt_tokens = _add_optional_count(run.prompt_tokens, telemetry.prompt_tokens)
            run.completion_tokens = _add_optional_count(
                run.completion_tokens,
                telemetry.completion_tokens,
            )
            run.total_tokens = _add_optional_count(run.total_tokens, telemetry.total_tokens)
            run.repair_request_count = (
                run.repair_request_count or 0
            ) + telemetry.repair_request_count
            run.fallback_count = (run.fallback_count or 0) + telemetry.fallback_count
            if telemetry.breaker_state is not None:
                run.breaker_state = telemetry.breaker_state

    async def store_validated_answer(
        self,
        state: Mapping[str, Any],
        answer: GroundedAnswer,
    ) -> str:
        self._validate_state_identity(state)
        fingerprint = _fingerprint_answer(answer)
        self._answers[fingerprint] = answer
        return fingerprint

    async def create_draft(
        self,
        state: Mapping[str, Any],
        answer_fingerprint: str,
    ) -> GraphDraftResult:
        answer = await self._load_answer(state, answer_fingerprint)
        request = CreateDraftArtifactInput(
            idempotency_key=self._tool_idempotency("draft"),
            answer_text=answer.answer_text,
            structured_fields=answer.structured_fields,
            citations=[
                DraftCitationInput(
                    chunk_id=citation.chunk_id,
                    document_version_id=citation.document_version_id,
                    excerpt=citation.excerpt,
                )
                for citation in answer.citations
            ],
            risk_hint=answer.risk_hint,
        )
        result = await self.mcp_client.create_draft_artifact(
            context_token=self._signed_token(ToolCapability.CREATE_DRAFT),
            request=request,
        )
        return GraphDraftResult(
            artifact_id=str(result.artifact_id),
            target_fingerprint=result.target_fingerprint,
        )

    async def assess_risk(self, state: Mapping[str, Any]) -> GraphRiskResult:
        if not self.context.publish_requested:
            return GraphRiskResult(requires_approval=False)
        # Publication is always treated as an external write. A model-provided
        # risk hint may enrich review context later, but it cannot bypass HITL.
        return GraphRiskResult(requires_approval=True)

    async def create_approval(
        self,
        state: Mapping[str, Any],
        draft: Any,
    ) -> GraphApprovalResult:
        artifact_id = UUID(draft.artifact_id)
        now = self.clock()
        async with self.session_factory.begin() as session:
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == self.context.run_id,
                    AgentRun.tenant_id == self.context.tenant_id,
                )
                .with_for_update()
            )
            artifact = await session.scalar(
                select(AgentArtifact).where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == self.context.tenant_id,
                    AgentArtifact.run_id == self.context.run_id,
                )
            )
            if run is None or artifact is None:
                raise AgentGraphError("The draft target is missing.")
            target_fingerprint = artifact_target_fingerprint(artifact)
            if target_fingerprint != draft.target_fingerprint:
                raise AgentGraphError("The draft target changed before approval.")
            existing = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.tenant_id == self.context.tenant_id,
                    ApprovalRequest.run_id == self.context.run_id,
                    ApprovalRequest.target_resource_id == artifact_id,
                    ApprovalRequest.target_fingerprint == target_fingerprint,
                    ApprovalRequest.operation == "publish_artifact",
                )
                .with_for_update()
            )
            if existing is not None:
                return GraphApprovalResult(approval_request_id=str(existing.id))
            approval = ApprovalRequest(
                tenant_id=self.context.tenant_id,
                run_id=self.context.run_id,
                requested_by_actor_id=self.context.actor_id,
                operation="publish_artifact",
                target_resource_type=TargetResourceType.ARTIFACT.value,
                target_resource_id=artifact_id,
                target_document_version_id=self.context.document_version_id,
                target_fingerprint=target_fingerprint,
                status=ApprovalRequestStatus.PENDING.value,
                decision_idempotency_key=None,
                requested_at=now,
                expires_at=now + timedelta(seconds=self.approval_ttl_seconds),
            )
            session.add(approval)
            await session.flush()
            return GraphApprovalResult(approval_request_id=str(approval.id))

    async def mark_waiting_for_approval(self) -> None:
        """Project the approval pause only after LangGraph persisted its interrupt."""
        now = self.clock()
        async with self.session_factory.begin() as session:
            run = await self._load_authorized_run(session, for_update=True)
            approval = await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.tenant_id == self.context.tenant_id,
                    ApprovalRequest.run_id == run.id,
                    ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
                )
                .order_by(ApprovalRequest.requested_at.desc())
                .with_for_update()
            )
            if approval is None:
                raise AgentGraphError("The pending approval is missing after checkpoint.")
            if run.status == AgentRunStatus.WAITING_APPROVAL.value:
                return
            if run.status != AgentRunStatus.RUNNING.value:
                raise AgentGraphError("The Agent run cannot enter approval wait state.")
            run.status = transition_agent_run(
                AgentRunStatus.RUNNING,
                AgentRunTransitionEvent.WAIT_FOR_APPROVAL,
            ).value
            run.waiting_at = now
            await append_agent_run_event(
                session,
                tenant_id=run.tenant_id,
                run_id=run.id,
                event_type="run.waiting_approval",
                payload={
                    "status": AgentRunStatus.WAITING_APPROVAL.value,
                    "approval_id": approval.id,
                },
                actor_id=run.actor_id,
            )

    async def validate_approval(
        self,
        state: Mapping[str, Any],
        decision: GraphApprovalDecision,
    ) -> None:
        approval_id = state.get("approval_request_id")
        artifact_fingerprint = state.get("artifact_fingerprint")
        if (
            approval_id != decision.approval_id
            or not isinstance(artifact_fingerprint, str)
            or decision.decision_fingerprint != self.context.approval_decision_fingerprint
        ):
            raise AgentGraphError("The approval target does not match the graph state.")
        async with self.session_factory() as session:
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == decision.approval_id,
                    ApprovalRequest.tenant_id == self.context.tenant_id,
                    ApprovalRequest.run_id == self.context.run_id,
                )
            )
            if approval is None:
                raise AgentGraphError("The approval target is unavailable.")
            if approval.target_fingerprint != artifact_fingerprint:
                raise AgentGraphError("The approval fingerprint does not match the draft.")
            expected_status = {
                "approved": ApprovalRequestStatus.APPROVED.value,
                "rejected": ApprovalRequestStatus.REJECTED.value,
                "expired": ApprovalRequestStatus.EXPIRED.value,
            }[decision.decision]
            if approval.status != expected_status:
                raise AgentGraphError("The approval decision is stale.")

    async def publish_artifact(self, state: Mapping[str, Any]) -> None:
        artifact_id = state.get("answer_artifact_id")
        target_fingerprint = state.get("artifact_fingerprint")
        if not isinstance(artifact_id, str) or not isinstance(target_fingerprint, str):
            raise AgentGraphError("The publication target is missing.")
        approval_id = self.context.approval_request_id
        if approval_id is None:
            raise AgentGraphError("Publication requires an approval binding.")
        request = PublishArtifactInput(
            idempotency_key=self._tool_idempotency("publish"),
            artifact_id=UUID(artifact_id),
            target_fingerprint=target_fingerprint,
        )
        await self.mcp_client.publish_artifact(
            context_token=self._signed_token(ToolCapability.PUBLISH, approval_id),
            request=request,
        )

    async def finalize(
        self,
        state: Mapping[str, Any],
        outcome: str,
        refusal_reason: str | None = None,
    ) -> None:
        now = self.clock()
        async with self.session_factory.begin() as session:
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == self.context.run_id,
                    AgentRun.tenant_id == self.context.tenant_id,
                )
                .with_for_update()
            )
            if run is None:
                raise AgentGraphError("Agent run is missing during finalization.")
            target_status = {
                "succeeded": AgentRunStatus.SUCCEEDED,
                "refused": AgentRunStatus.REFUSED,
                "rejected": AgentRunStatus.REJECTED,
                "expired": AgentRunStatus.EXPIRED,
            }.get(outcome)
            if target_status is None:
                raise AgentGraphError("The graph returned an unsupported outcome.")
            if run.status == target_status.value:
                return
            try:
                transition_event = {
                    AgentRunStatus.SUCCEEDED: AgentRunTransitionEvent.SUCCEED,
                    AgentRunStatus.REFUSED: AgentRunTransitionEvent.REFUSE,
                    AgentRunStatus.REJECTED: AgentRunTransitionEvent.REJECT,
                    AgentRunStatus.EXPIRED: AgentRunTransitionEvent.EXPIRE,
                }[target_status]
                run.status = transition_agent_run(
                    AgentRunStatus(run.status), transition_event
                ).value
            except ValueError as error:
                raise AgentGraphError(
                    "Agent run cannot be finalized from its current state."
                ) from error
            run.finished_at = now
            await append_agent_run_event(
                session,
                tenant_id=run.tenant_id,
                run_id=run.id,
                event_type="run.finished",
                payload={"status": target_status.value, "refusal_reason": refusal_reason},
                actor_id=run.actor_id,
            )

    async def _load_input_text(self) -> str:
        async with self.session_factory() as session:
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.id == self.context.run_id,
                    AgentRun.tenant_id == self.context.tenant_id,
                )
            )
            if run is None:
                raise AgentGraphError("Agent run is missing.")
            return run.input_text

    async def _load_authorized_run(
        self,
        session: AsyncSession,
        *,
        for_update: bool = False,
    ) -> AgentRun:
        statement = select(AgentRun).where(
            AgentRun.id == self.context.run_id,
            AgentRun.tenant_id == self.context.tenant_id,
            AgentRun.actor_id == self.context.actor_id,
            AgentRun.document_version_id == self.context.document_version_id,
            AgentRun.graph_thread_id == self.context.graph_thread_id,
            AgentRun.graph_version == self.context.graph_version,
            AgentRun.current_execution_seq == self.context.execution_sequence,
        )
        if for_update:
            statement = statement.with_for_update()
        run = await session.scalar(statement)
        membership = await session.scalar(
            select(Membership)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == self.context.tenant_id,
                Membership.user_id == self.context.actor_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        execution = await session.scalar(
            select(AgentRunExecution).where(
                AgentRunExecution.id == self.context.execution_id,
                AgentRunExecution.tenant_id == self.context.tenant_id,
                AgentRunExecution.run_id == self.context.run_id,
                AgentRunExecution.sequence == self.context.execution_sequence,
                AgentRunExecution.job_id == self.context.job_id,
            )
        )
        job = await session.scalar(
            select(Job).where(
                Job.id == self.context.job_id,
                Job.tenant_id == self.context.tenant_id,
                Job.status == JobStatus.RUNNING.value,
                Job.lease_token == self.context.lease_token,
                Job.fencing_token == self.context.fencing_token,
            )
        )
        attempt = await session.scalar(
            select(JobAttempt).where(
                JobAttempt.id == self.context.attempt_id,
                JobAttempt.tenant_id == self.context.tenant_id,
                JobAttempt.job_id == self.context.job_id,
                JobAttempt.status == JobAttemptStatus.RUNNING.value,
                JobAttempt.lease_token == self.context.lease_token,
                JobAttempt.fencing_token == self.context.fencing_token,
            )
        )
        if run is None or membership is None or execution is None or job is None or attempt is None:
            raise AgentGraphError("The execution is no longer authorized.")
        await self._load_authorized_version(session)
        return run

    async def _load_authorized_version(self, session: AsyncSession) -> DocumentVersion:
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == self.context.document_version_id,
                DocumentVersion.tenant_id == self.context.tenant_id,
                DocumentVersion.status == DocumentVersionStatus.READY.value,
            )
        )
        generation_id = await self._load_generation_id(session)
        generation = await session.scalar(
            select(DocumentIngestionGeneration).where(
                DocumentIngestionGeneration.id == generation_id,
                DocumentIngestionGeneration.tenant_id == self.context.tenant_id,
                DocumentIngestionGeneration.document_version_id == self.context.document_version_id,
                DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                DocumentIngestionGeneration.active.is_(True),
            )
        )
        if version is None or generation is None:
            raise AgentGraphError("The document version is not authorized for execution.")
        return version

    async def _load_evidence_rows(
        self,
        session: AsyncSession,
    ) -> tuple[AgentRun, DocumentVersion, list[tuple[AgentRunEvidence, DocumentChunk]]]:
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.id == self.context.run_id,
                AgentRun.tenant_id == self.context.tenant_id,
            )
        )
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == self.context.document_version_id,
                DocumentVersion.tenant_id == self.context.tenant_id,
            )
        )
        if run is None or version is None:
            raise AgentGraphError("The run or document version is missing.")
        rows = (
            await session.execute(
                select(AgentRunEvidence, DocumentChunk)
                .join(DocumentChunk, DocumentChunk.id == AgentRunEvidence.chunk_id)
                .where(
                    AgentRunEvidence.run_id == run.id,
                    AgentRunEvidence.tenant_id == run.tenant_id,
                    AgentRunEvidence.document_version_id == version.id,
                    AgentRunEvidence.generation_id == run.index_generation_id,
                    DocumentChunk.tenant_id == run.tenant_id,
                    DocumentChunk.document_version_id == version.id,
                    DocumentChunk.generation_id == run.index_generation_id,
                )
                .order_by(AgentRunEvidence.rank)
            )
        ).all()
        if not rows:
            raise AgentGraphError("The run has no frozen evidence.")
        return run, version, [(row[0], row[1]) for row in rows]

    async def _load_answer(self, state: Mapping[str, Any], fingerprint: str) -> GroundedAnswer:
        answer = self._answers.get(fingerprint)
        if answer is not None:
            return answer
        model_fingerprint = state.get("model_output_fingerprint")
        if not isinstance(model_fingerprint, str):
            raise AgentGraphError("The model output fingerprint is missing during recovery.")
        request = await self.build_model_request(state)
        output = await self.load_model_output(state, model_fingerprint)
        validated = validate_grounded_output(
            output,
            request=request,
            tenant_id=self.context.tenant_id,
            document_version_id=self.context.document_version_id,
        )
        if isinstance(validated, GroundedRefusal):
            raise AgentGraphError("A refusal cannot recover a validated answer segment.")
        if _fingerprint_answer(validated) != fingerprint:
            raise AgentGraphError("The validated answer changed during recovery.")
        self._answers[fingerprint] = validated
        return validated

    async def _load_generation_id(self, session: AsyncSession) -> UUID | None:
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.id == self.context.run_id,
                AgentRun.tenant_id == self.context.tenant_id,
            )
        )
        return run.index_generation_id if run is not None else None

    def _validate_state_identity(self, state: Mapping[str, Any]) -> None:
        if (
            str(state.get("run_id")) != str(self.context.run_id)
            or str(state.get("tenant_id")) != str(self.context.tenant_id)
            or str(state.get("actor_id")) != str(self.context.actor_id)
            or str(state.get("document_version_id")) != str(self.context.document_version_id)
            or str(state.get("graph_version")) != self.context.graph_version
        ):
            raise AgentGraphError("The graph state is not bound to the execution context.")

    def _tool_idempotency(self, operation: str) -> str:
        return (
            f"agent:{self.context.run_id}:execution:{self.context.execution_sequence}:{operation}"
        )

    def _signed_context(
        self,
        capability: ToolCapability,
        approval_id: UUID | None = None,
    ) -> SignedExecutionContext:
        now = self.clock()
        return SignedExecutionContext(
            tenant_id=self.context.tenant_id,
            actor_id=self.context.actor_id,
            run_id=self.context.run_id,
            execution_id=self.context.execution_id,
            job_id=self.context.job_id,
            attempt_id=self.context.attempt_id,
            lease_token=self.context.lease_token,
            fencing_token=self.context.fencing_token,
            capabilities=(capability,),
            target_document_version_id=self.context.document_version_id,
            approval_request_id=approval_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.mcp_settings.context_ttl_seconds),
            nonce=f"{self.context.attempt_id.hex}_{operation_nonce(capability)}",
        )

    def _signed_token(self, capability: ToolCapability, approval_id: UUID | None = None) -> str:
        return sign_execution_context(
            self._signed_context(capability, approval_id),
            self.mcp_settings.signing_secret,
        )


def operation_nonce(capability: ToolCapability) -> str:
    return hashlib.sha256(capability.value.encode("ascii")).hexdigest()[:16]


def _add_optional_count(current: int | None, increment: int | None) -> int | None:
    if increment is None:
        return current
    return (current or 0) + increment


def _fingerprint_model_output(output: GroundedModelOutput) -> str:
    return _sha256(
        {
            "payload": output.payload.model_dump(mode="json"),
            "identity": output.identity.model_dump(mode="json"),
            "repaired": output.repaired,
        }
    )


def _fingerprint_answer(answer: GroundedAnswer) -> str:
    return _sha256(
        {
            "task_type": answer.task_type.value,
            "answer_text": answer.answer_text,
            "structured_fields": answer.structured_fields,
            "citations": [
                {
                    "chunk_id": str(citation.chunk_id),
                    "document_version_id": str(citation.document_version_id),
                    "source_filename": citation.source_filename,
                    "page_number": citation.page_number,
                    "heading": citation.heading,
                    "start_offset": citation.start_offset,
                    "end_offset": citation.end_offset,
                    "excerpt": citation.excerpt,
                }
                for citation in answer.citations
            ],
            "risk_hint": answer.risk_hint.value if answer.risk_hint is not None else None,
            "identity": answer.identity.model_dump(mode="json"),
            "repaired": answer.repaired,
        }
    )


def _sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["DurableAgentGraphBackend"]
