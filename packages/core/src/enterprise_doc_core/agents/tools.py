from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.execution_context import SignedExecutionContext, ToolCapability
from enterprise_doc_core.agents.grounding import GroundingValidationError, validate_grounded_output
from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentRun,
    AgentRunEvidence,
    AgentRunStatus,
    AgentRunTaskType,
    ApprovalRequestStatus,
    ToolExecution,
    ToolExecutionStatus,
)
from enterprise_doc_core.agents.policy import (
    AuthorizedToolScope,
    TargetResourceType,
    ToolApprovalError,
    ToolPolicyError,
    artifact_target_fingerprint,
    reload_tool_policy,
)
from enterprise_doc_core.agents.schemas import (
    BehaviorVersions,
    CitationProposal,
    GroundedEvidence,
    GroundedModelOutput,
    GroundedModelPayload,
    GroundedModelRequest,
    GroundedRefusal,
    ModelIdentity,
    QuestionAnswerModelOutput,
    RiskHint,
    StructuredExtractionModelOutput,
    StructuredExtractionSchema,
    SummaryModelOutput,
)
from enterprise_doc_core.agents.service import append_agent_run_event
from enterprise_doc_core.agents.state import (
    AgentArtifactEvent,
    AgentRunTransitionEvent,
    ApprovalRequestEvent,
    ToolExecutionEvent,
    transition_agent_artifact,
    transition_agent_run,
    transition_approval_request,
    transition_tool_execution,
)
from enterprise_doc_core.documents.models import DocumentChunk, DocumentVersion
from enterprise_doc_core.documents.retrieval import RefusalReason, RetrievalDecision
from enterprise_doc_core.object_store import ArtifactObjectStore

_SEARCH_INTERRUPTED_ERROR_CODE = "search_execution_interrupted"


class ToolExecutionError(RuntimeError):
    code = "tool_execution_error"
    retryable = False

    def __init__(self, message: str = "The tool execution could not be completed.") -> None:
        super().__init__(message)


class ToolInputInvalid(ToolExecutionError):
    code = "tool_input_invalid"


class ToolIdempotencyConflict(ToolExecutionError):
    code = "tool_idempotency_conflict"


class ToolExecutionInProgress(ToolExecutionError):
    code = "tool_execution_in_progress"
    retryable = True


class ToolPriorFailure(ToolExecutionError):
    code = "tool_prior_failure"


class ToolResultInvalid(ToolExecutionError):
    code = "tool_result_invalid"


class ToolArtifactIntegrityError(ToolExecutionError):
    code = "tool_artifact_integrity_error"


class ToolObjectStoreUnavailable(ToolExecutionError):
    code = "tool_object_store_unavailable"
    retryable = True


class ToolName(StrEnum):
    SEARCH_DOCUMENT = "search_document"
    READ_CHUNK = "read_chunk"
    CREATE_DRAFT_ARTIFACT = "create_draft_artifact"
    GET_ARTIFACT = "get_artifact"
    PUBLISH_ARTIFACT = "publish_artifact"


class _ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be blank")
        return normalized


class SearchDocumentInput(_ToolModel):
    query: str = Field(min_length=1, max_length=20_000)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class ReadChunkInput(_ToolModel):
    chunk_id: UUID


class DraftCitationInput(CitationProposal):
    pass


class CreateDraftArtifactInput(_ToolModel):
    answer_text: str = Field(min_length=1, max_length=100_000)
    structured_fields: dict[str, JsonValue] | None = None
    citations: list[DraftCitationInput] = Field(min_length=1, max_length=50)
    risk_hint: RiskHint | None = None
    kind: Literal["answer"] = "answer"


class GetArtifactInput(_ToolModel):
    artifact_id: UUID
    expires_in_seconds: int = Field(default=300, ge=1, le=3600)


class PublishArtifactInput(_ToolModel):
    artifact_id: UUID
    target_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SearchCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_version_id: UUID
    generation_id: UUID
    text: str = Field(min_length=1, max_length=200_000)
    rank: int = Field(ge=1)
    score: float
    page_number: int | None = None
    heading: str | None = None
    source_filename: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)


class SearchDocumentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    replayed: bool
    accepted: bool
    refusal_reason: RefusalReason | None
    candidates: tuple[SearchCandidateResult, ...]


class ReadChunkResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    replayed: bool
    chunk_id: UUID
    document_version_id: UUID
    generation_id: UUID
    text: str
    content_sha256: str
    page_number: int | None
    heading: str | None
    source_filename: str | None
    start_offset: int
    end_offset: int


class CreateDraftArtifactResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    replayed: bool
    artifact_id: UUID
    status: AgentArtifactStatus
    content_sha256: str
    size_bytes: int
    target_fingerprint: str


class GetArtifactResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    replayed: bool
    artifact_id: UUID
    status: AgentArtifactStatus
    content_sha256: str
    size_bytes: int
    url: str
    expires_in_seconds: int


class PublishArtifactResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    replayed: bool
    artifact_id: UUID
    status: AgentArtifactStatus
    target_fingerprint: str
    published_at: datetime


class RetrievalService(Protocol):
    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None = None,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision: ...


@dataclass(frozen=True, slots=True)
class _BeginResult:
    execution_id: UUID
    replayed: bool
    recovering: bool
    lease_started_at: datetime | None


class AgentToolService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        retrieval_service: RetrievalService,
        artifact_store: ArtifactObjectStore | None = None,
        clock: Any = None,
        stale_execution_seconds: int = 30,
        max_search_results: int = 50,
        artifact_bucket: str = "artifacts",
    ) -> None:
        if stale_execution_seconds < 1:
            raise ValueError("stale_execution_seconds must be positive")
        if not 1 <= max_search_results <= 100:
            raise ValueError("max_search_results must be between 1 and 100")
        self.session_factory = session_factory
        self.retrieval_service = retrieval_service
        self.artifact_store = artifact_store
        self.clock = clock or (lambda: datetime.now(UTC))
        self.stale_execution_seconds = stale_execution_seconds
        self.max_search_results = max_search_results
        self.artifact_bucket = artifact_bucket

    async def search_document(
        self,
        context: SignedExecutionContext,
        request: SearchDocumentInput,
    ) -> SearchDocumentResult:
        begin = await self._begin(
            context=context,
            tool=ToolName.SEARCH_DOCUMENT,
            capability=ToolCapability.READ_EVIDENCE,
            request=request,
        )
        if begin.replayed:
            return await self._replay_search(context=context, execution_id=begin.execution_id)
        if begin.recovering:
            try:
                recovered = await self._recover_search(
                    context=context,
                    execution_id=begin.execution_id,
                    expected_started_at=begin.lease_started_at,
                )
            except asyncio.CancelledError:
                await self._interrupt_search(
                    begin.execution_id,
                    context.tenant_id,
                    expected_started_at=begin.lease_started_at,
                )
                raise
            if recovered is not None:
                return recovered
        try:
            retrieval_kwargs: dict[str, object] = {
                "tenant_id": context.tenant_id,
                "document_version_id": context.target_document_version_id,
                "query": request.query,
            }
            if "actor_id" in inspect.signature(self.retrieval_service.retrieve).parameters:
                retrieval_kwargs["actor_id"] = context.actor_id
            decision = await self.retrieval_service.retrieve(**retrieval_kwargs)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            await self._interrupt_search(
                begin.execution_id,
                context.tenant_id,
                expected_started_at=begin.lease_started_at,
            )
            raise
        except Exception as error:
            await self._fail(
                begin.execution_id,
                context.tenant_id,
                "retrieval_failed",
                expected_started_at=begin.lease_started_at,
            )
            raise ToolExecutionError() from error
        try:
            result = await self._freeze_search_and_succeed(
                context=context,
                execution_id=begin.execution_id,
                decision=decision,
                expected_started_at=begin.lease_started_at,
            )
        except asyncio.CancelledError:
            await self._interrupt_search(
                begin.execution_id,
                context.tenant_id,
                expected_started_at=begin.lease_started_at,
            )
            raise
        except ToolPolicyError:
            await self._deny(
                begin.execution_id,
                context.tenant_id,
                "tool_policy_denied",
                expected_started_at=begin.lease_started_at,
            )
            raise
        except ToolExecutionError:
            await self._fail(
                begin.execution_id,
                context.tenant_id,
                "tool_result_invalid",
                expected_started_at=begin.lease_started_at,
            )
            raise
        return result

    async def read_chunk(
        self,
        context: SignedExecutionContext,
        request: ReadChunkInput,
    ) -> ReadChunkResult:
        begin = await self._begin(
            context=context,
            tool=ToolName.READ_CHUNK,
            capability=ToolCapability.READ_EVIDENCE,
            request=request,
            chunk_id=request.chunk_id,
        )
        try:
            result = await self._read_chunk_result(
                context=context,
                execution_id=begin.execution_id,
                chunk_id=request.chunk_id,
                replayed=begin.replayed,
            )
        except ToolPolicyError:
            if not begin.replayed:
                await self._deny(begin.execution_id, context.tenant_id, "tool_policy_denied")
            raise
        except ToolExecutionError:
            if not begin.replayed:
                await self._fail(begin.execution_id, context.tenant_id, "read_failed")
            raise
        if not begin.replayed:
            await self._succeed(
                execution_id=begin.execution_id,
                tenant_id=context.tenant_id,
                result_summary={"chunk_id": str(request.chunk_id)},
            )
        return result

    async def create_draft_artifact(
        self,
        context: SignedExecutionContext,
        request: CreateDraftArtifactInput,
    ) -> CreateDraftArtifactResult:
        if self.artifact_store is None:
            raise ToolExecutionError("artifact store is not configured")
        begin = await self._begin(
            context=context,
            tool=ToolName.CREATE_DRAFT_ARTIFACT,
            capability=ToolCapability.CREATE_DRAFT,
            request=request,
        )
        if begin.replayed:
            return await self._replay_draft(context, begin.execution_id)
        try:
            return await self._write_and_finalize_draft(
                context=context,
                execution_id=begin.execution_id,
                request=request,
                replayed=begin.recovering,
            )
        except ToolPolicyError:
            await self._deny(begin.execution_id, context.tenant_id, "tool_policy_denied")
            raise
        except ToolExecutionError:
            await self._fail_draft(begin.execution_id, context.tenant_id, "draft_failed")
            raise
        except Exception as error:
            # Leave the execution recoverable after a transient object-store or
            # finalize failure. The deterministic artifact key/body are reused on retry.
            raise ToolObjectStoreUnavailable() from error

    async def _write_and_finalize_draft(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        request: CreateDraftArtifactInput,
        replayed: bool,
    ) -> CreateDraftArtifactResult:
        assert self.artifact_store is not None
        artifact, body = await self._prepare_draft(
            context=context,
            execution_id=execution_id,
            request=request,
        )
        stored = await self.artifact_store.put_object(
            bucket=self.artifact_bucket,
            key=artifact.object_key,
            body=body,
            content_type=artifact.content_type,
            metadata={"kind": artifact.kind, "run-id": str(artifact.run_id)},
        )
        await self._verify_stored_object(
            bucket=artifact.object_bucket,
            key=artifact.object_key,
            content_sha256=stored.content_sha256,
            size_bytes=stored.size_bytes,
        )
        return await self._finalize_draft(
            context=context,
            execution_id=execution_id,
            artifact_id=artifact.id,
            stored_sha256=stored.content_sha256,
            stored_size=stored.size_bytes,
            replayed=replayed,
        )

    async def get_artifact(
        self,
        context: SignedExecutionContext,
        request: GetArtifactInput,
    ) -> GetArtifactResult:
        if self.artifact_store is None:
            raise ToolExecutionError("artifact store is not configured")
        begin = await self._begin(
            context=context,
            tool=ToolName.GET_ARTIFACT,
            capability=ToolCapability.READ_ARTIFACT,
            request=request,
            artifact_id=request.artifact_id,
        )
        try:
            return await self._get_artifact_result(
                context=context,
                execution_id=begin.execution_id,
                artifact_id=request.artifact_id,
                expires_in_seconds=request.expires_in_seconds,
                replayed=begin.replayed,
                mark_success=not begin.replayed,
            )
        except ToolPolicyError:
            if not begin.replayed:
                await self._deny(begin.execution_id, context.tenant_id, "tool_policy_denied")
            raise
        except ToolExecutionError:
            if not begin.replayed:
                await self._fail(begin.execution_id, context.tenant_id, "artifact_read_failed")
            raise

    async def publish_artifact(
        self,
        context: SignedExecutionContext,
        request: PublishArtifactInput,
    ) -> PublishArtifactResult:
        if self.artifact_store is None:
            raise ToolExecutionError("artifact store is not configured")
        begin = await self._begin(
            context=context,
            tool=ToolName.PUBLISH_ARTIFACT,
            capability=ToolCapability.PUBLISH,
            request=request,
            artifact_id=request.artifact_id,
            target_fingerprint=request.target_fingerprint,
        )
        if begin.replayed:
            return await self._replay_publish(context, begin.execution_id, request.artifact_id)
        try:
            async with self.session_factory.begin() as session:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"agent-run:{context.tenant_id}:{context.run_id}"},
                )
                scope = await reload_tool_policy(
                    session,
                    context=context,
                    capability=ToolCapability.PUBLISH,
                    artifact_id=request.artifact_id,
                    target_fingerprint=request.target_fingerprint,
                    now=self.clock(),
                    for_update=True,
                )
                assert scope.artifact is not None
                assert scope.approval is not None
                if scope.run.status != AgentRunStatus.RUNNING.value:
                    raise ToolApprovalError()
                await self._verify_object_head(scope.artifact)
                now = self.clock()
                scope.artifact.status = transition_agent_artifact(
                    AgentArtifactStatus(scope.artifact.status),
                    AgentArtifactEvent.PUBLISH,
                ).value
                scope.artifact.published_at = now
                scope.approval.status = transition_approval_request(
                    ApprovalRequestStatus(scope.approval.status),
                    ApprovalRequestEvent.CONSUME,
                ).value
                scope.approval.consumed_at = now
                scope.run.status = transition_agent_run(
                    AgentRunStatus(scope.run.status),
                    AgentRunTransitionEvent.SUCCEED,
                ).value
                scope.run.finished_at = now
                await append_agent_run_event(
                    session,
                    tenant_id=scope.run.tenant_id,
                    run_id=scope.run.id,
                    event_type="run.finished",
                    payload={
                        "status": AgentRunStatus.SUCCEEDED.value,
                        "refusal_reason": None,
                    },
                    actor_id=scope.run.actor_id,
                )
                execution = await self._lock_execution(
                    session,
                    execution_id=begin.execution_id,
                    tenant_id=context.tenant_id,
                )
                self._mark_execution_succeeded(
                    execution,
                    result_summary={
                        "artifact_id": str(scope.artifact.id),
                        "target_fingerprint": request.target_fingerprint,
                        "status": AgentArtifactStatus.PUBLISHED.value,
                    },
                    now=now,
                )
                return PublishArtifactResult(
                    execution_id=execution.id,
                    replayed=False,
                    artifact_id=scope.artifact.id,
                    status=AgentArtifactStatus.PUBLISHED,
                    target_fingerprint=request.target_fingerprint,
                    published_at=now,
                )
        except ToolPolicyError:
            await self._deny(begin.execution_id, context.tenant_id, "tool_policy_denied")
            raise
        except ToolExecutionError:
            await self._fail(begin.execution_id, context.tenant_id, "publish_failed")
            raise
        except Exception as error:
            await self._fail(begin.execution_id, context.tenant_id, "publish_failed")
            raise ToolExecutionError() from error

    async def _begin(
        self,
        *,
        context: SignedExecutionContext,
        tool: ToolName,
        capability: ToolCapability,
        request: _ToolModel,
        chunk_id: UUID | None = None,
        artifact_id: UUID | None = None,
        target_fingerprint: str | None = None,
    ) -> _BeginResult:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        input_hash = _input_sha256(request)
        fingerprint = _tool_request_fingerprint(
            context=context,
            tool=tool,
            input_sha256=input_hash,
        )
        denial: ToolPolicyError | None = None
        result: _BeginResult | None = None
        async with self.session_factory.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"tool:{context.tenant_id}:{request.idempotency_key}"},
            )
            existing = await session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.tenant_id == context.tenant_id,
                    ToolExecution.idempotency_key == request.idempotency_key,
                )
                .with_for_update()
            )
            allow_succeeded_publish_replay = bool(
                tool is ToolName.PUBLISH_ARTIFACT
                and existing is not None
                and existing.tool_name == tool.value
                and existing.capability == capability.value
                and existing.request_fingerprint == fingerprint
                and existing.status == ToolExecutionStatus.SUCCEEDED.value
            )
            try:
                await reload_tool_policy(
                    session,
                    context=context,
                    capability=capability,
                    chunk_id=chunk_id,
                    artifact_id=artifact_id,
                    target_fingerprint=target_fingerprint,
                    now=now,
                    for_update=False,
                    allow_succeeded_publish_replay=allow_succeeded_publish_replay,
                )
            except ToolPolicyError as error:
                denial = error
                if existing is None:
                    run_id = await session.scalar(
                        select(AgentRun.id).where(
                            AgentRun.id == context.run_id,
                            AgentRun.tenant_id == context.tenant_id,
                        )
                    )
                    if run_id is not None:
                        session.add(
                            ToolExecution(
                                tenant_id=context.tenant_id,
                                run_id=context.run_id,
                                tool_name=tool.value,
                                capability=capability.value,
                                idempotency_key=request.idempotency_key,
                                request_fingerprint=fingerprint,
                                input_sha256=input_hash,
                                target_resource_type=(
                                    TargetResourceType.ARTIFACT.value if artifact_id else None
                                ),
                                target_resource_id=artifact_id,
                                target_version=str(context.target_document_version_id),
                                approval_request_id=context.approval_request_id,
                                status=ToolExecutionStatus.DENIED.value,
                                error_code=error.code,
                                finished_at=now,
                            )
                        )
                elif existing.request_fingerprint != fingerprint:
                    raise ToolIdempotencyConflict() from error
            if denial is None:
                if existing is not None:
                    if existing.request_fingerprint != fingerprint:
                        raise ToolIdempotencyConflict()
                    if existing.status == ToolExecutionStatus.SUCCEEDED.value:
                        result = _BeginResult(
                            existing.id,
                            replayed=True,
                            recovering=False,
                            lease_started_at=None,
                        )
                    elif existing.status in {
                        ToolExecutionStatus.PENDING.value,
                        ToolExecutionStatus.RUNNING.value,
                    }:
                        interrupted = existing.error_code == _SEARCH_INTERRUPTED_ERROR_CODE
                        age = (now - (existing.started_at or existing.created_at)).total_seconds()
                        if not interrupted and age < self.stale_execution_seconds:
                            raise ToolExecutionInProgress()
                        lease_started_at = _next_lease_started_at(existing.started_at, now)
                        if existing.status == ToolExecutionStatus.PENDING.value:
                            existing.status = transition_tool_execution(
                                ToolExecutionStatus.PENDING,
                                ToolExecutionEvent.BEGIN,
                            ).value
                        existing.started_at = lease_started_at
                        existing.error_code = None
                        result = _BeginResult(
                            existing.id,
                            replayed=False,
                            recovering=not interrupted,
                            lease_started_at=lease_started_at,
                        )
                    else:
                        raise ToolPriorFailure()
                else:
                    execution = ToolExecution(
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        tool_name=tool.value,
                        capability=capability.value,
                        idempotency_key=request.idempotency_key,
                        request_fingerprint=fingerprint,
                        input_sha256=input_hash,
                        target_resource_type=(
                            TargetResourceType.ARTIFACT.value if artifact_id else None
                        ),
                        target_resource_id=artifact_id,
                        target_version=str(context.target_document_version_id),
                        approval_request_id=context.approval_request_id,
                        status=ToolExecutionStatus.PENDING.value,
                        started_at=now,
                    )
                    execution.status = transition_tool_execution(
                        ToolExecutionStatus.PENDING,
                        ToolExecutionEvent.BEGIN,
                    ).value
                    session.add(execution)
                    await session.flush()
                    result = _BeginResult(
                        execution.id,
                        replayed=False,
                        recovering=False,
                        lease_started_at=now,
                    )
        if denial is not None:
            raise denial
        assert result is not None
        return result

    async def _freeze_search_and_succeed(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        decision: RetrievalDecision,
        expected_started_at: datetime | None,
    ) -> SearchDocumentResult:
        candidates = tuple(decision.candidates[: self.max_search_results])
        now = self.clock()
        async with self.session_factory.begin() as session:
            await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.READ_EVIDENCE,
                now=now,
                for_update=True,
            )
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=context.tenant_id,
            )
            if (
                execution.status != ToolExecutionStatus.RUNNING.value
                or execution.started_at != expected_started_at
            ):
                raise ToolExecutionInProgress()
            existing_evidence = (
                await session.scalars(
                    select(AgentRunEvidence)
                    .where(
                        AgentRunEvidence.tenant_id == context.tenant_id,
                        AgentRunEvidence.run_id == context.run_id,
                    )
                    .order_by(AgentRunEvidence.rank)
                )
            ).all()
            if existing_evidence:
                raise ToolResultInvalid("evidence is already frozen for this run")
            if decision.accepted:
                chunk_rows = (
                    await session.scalars(
                        select(DocumentChunk).where(
                            DocumentChunk.tenant_id == context.tenant_id,
                            DocumentChunk.document_version_id == context.target_document_version_id,
                            DocumentChunk.id.in_([candidate.chunk_id for candidate in candidates]),
                        )
                    )
                ).all()
                chunks_by_id = {chunk.id: chunk for chunk in chunk_rows}
                for rank, candidate in enumerate(candidates, start=1):
                    chunk = chunks_by_id.get(candidate.chunk_id)
                    if (
                        candidate.tenant_id != context.tenant_id
                        or candidate.document_version_id != context.target_document_version_id
                        or chunk is None
                        or chunk.generation_id != candidate.generation_id
                        or chunk.normalized_text != candidate.text
                        or chunk.content_sha256
                        != hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()
                        or candidate.score <= 0
                    ):
                        raise ToolResultInvalid()
                    session.add(
                        AgentRunEvidence(
                            tenant_id=context.tenant_id,
                            run_id=context.run_id,
                            chunk_id=candidate.chunk_id,
                            document_version_id=candidate.document_version_id,
                            generation_id=candidate.generation_id,
                            rank=rank,
                            rrf_score=candidate.score,
                            content_sha256=hashlib.sha256(
                                candidate.text.encode("utf-8")
                            ).hexdigest(),
                        )
                    )
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=context.tenant_id,
            )
            summary: dict[str, Any] = {
                "accepted": decision.accepted,
                "refusal_reason": (
                    decision.refusal_reason.value if decision.refusal_reason is not None else None
                ),
                "count": len(candidates),
            }
            self._mark_execution_succeeded(execution, result_summary=summary, now=now)
        return SearchDocumentResult(
            execution_id=execution_id,
            replayed=False,
            accepted=decision.accepted,
            refusal_reason=decision.refusal_reason,
            candidates=tuple(
                _candidate_result(candidate, rank=index)
                for index, candidate in enumerate(candidates, 1)
            ),
        )

    async def _read_chunk_result(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        chunk_id: UUID,
        replayed: bool,
    ) -> ReadChunkResult:
        async with self.session_factory() as session:
            scope = await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.READ_EVIDENCE,
                chunk_id=chunk_id,
            )
            assert scope.chunk is not None and scope.evidence is not None
            return ReadChunkResult(
                execution_id=execution_id,
                replayed=replayed,
                chunk_id=scope.chunk.id,
                document_version_id=scope.chunk.document_version_id,
                generation_id=scope.chunk.generation_id,
                text=scope.chunk.normalized_text,
                content_sha256=scope.chunk.content_sha256,
                page_number=scope.chunk.page_number,
                heading=scope.chunk.heading,
                source_filename=scope.document_version.original_filename,
                start_offset=scope.chunk.start_offset,
                end_offset=scope.chunk.end_offset,
            )

    async def _prepare_draft(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        request: CreateDraftArtifactInput,
    ) -> tuple[AgentArtifact, bytes]:
        async with self.session_factory.begin() as session:
            scope = await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.CREATE_DRAFT,
                now=self.clock(),
                for_update=True,
            )
            _answer, body = await self._validated_draft(scope, request, session)
            existing = await session.scalar(
                select(AgentArtifact)
                .where(
                    AgentArtifact.tenant_id == context.tenant_id,
                    AgentArtifact.run_id == context.run_id,
                    AgentArtifact.kind == request.kind,
                )
                .with_for_update()
            )
            content_sha256 = hashlib.sha256(body).hexdigest()
            size_bytes = len(body)
            if existing is not None:
                if existing.status == AgentArtifactStatus.WRITING.value:
                    if (
                        existing.content_sha256 is not None
                        and existing.content_sha256 != content_sha256
                    ) or (existing.size_bytes is not None and existing.size_bytes != size_bytes):
                        raise ToolResultInvalid("the run already has a different draft artifact")
                    existing.content_sha256 = content_sha256
                    existing.size_bytes = size_bytes
                elif (
                    existing.status != AgentArtifactStatus.DRAFT_READY.value
                    or existing.content_sha256 != content_sha256
                    or existing.size_bytes != size_bytes
                ):
                    raise ToolResultInvalid("the run already has a different draft artifact")
                artifact = existing
            else:
                artifact_id = uuid4()
                artifact = AgentArtifact(
                    id=artifact_id,
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    source_document_version_id=context.target_document_version_id,
                    kind=request.kind,
                    status=AgentArtifactStatus.WRITING.value,
                    content_type="application/json",
                    object_bucket=self.artifact_bucket,
                    object_key=(
                        f"{context.tenant_id}/agent-runs/{context.run_id}/"
                        f"{artifact_id}/{request.kind}.json"
                    ),
                    content_sha256=content_sha256,
                    size_bytes=size_bytes,
                    behavior_versions={
                        "graph_version": scope.run.graph_version,
                        "prompt_version": scope.run.prompt_version,
                        "tool_schema_version": scope.run.tool_schema_version,
                    },
                )
                session.add(artifact)
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=context.tenant_id,
            )
            execution.target_resource_type = TargetResourceType.ARTIFACT.value
            execution.target_resource_id = artifact.id
            execution.target_version = str(context.target_document_version_id)
            await session.flush()
            return artifact, body

    async def _validated_draft(
        self,
        scope: AuthorizedToolScope,
        request: CreateDraftArtifactInput,
        session: AsyncSession,
    ) -> tuple[object, bytes]:
        evidence_rows = (
            await session.execute(
                select(AgentRunEvidence, DocumentChunk, DocumentVersion)
                .join(DocumentChunk, DocumentChunk.id == AgentRunEvidence.chunk_id)
                .join(DocumentVersion, DocumentVersion.id == AgentRunEvidence.document_version_id)
                .where(
                    AgentRunEvidence.tenant_id == scope.context.tenant_id,
                    AgentRunEvidence.run_id == scope.run.id,
                )
                .order_by(AgentRunEvidence.rank)
            )
        ).all()
        if not evidence_rows:
            raise ToolInputInvalid("draft requires frozen evidence")
        grounded_evidence = [
            GroundedEvidence(
                chunk_id=evidence.chunk_id,
                tenant_id=evidence.tenant_id,
                document_version_id=evidence.document_version_id,
                generation_id=evidence.generation_id,
                text=chunk.normalized_text,
                rank=evidence.rank,
                score=evidence.rrf_score,
                page_number=chunk.page_number,
                heading=chunk.heading,
                source_filename=version.original_filename,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
            for evidence, chunk, version in evidence_rows
        ]
        extraction_schema = (
            StructuredExtractionSchema.model_validate(scope.run.extraction_schema)
            if scope.run.extraction_schema is not None
            else None
        )
        request_model = GroundedModelRequest(
            task_type=AgentRunTaskType(scope.run.task_type),
            user_input=scope.run.input_text,
            evidence=grounded_evidence,
            extraction_schema=extraction_schema,
            behavior_versions=BehaviorVersions(
                graph_version=scope.run.graph_version,
                prompt_version=scope.run.prompt_version,
                tool_schema_version=scope.run.tool_schema_version,
            ),
        )
        citations = [
            CitationProposal.model_validate(citation.model_dump()) for citation in request.citations
        ]
        payload: GroundedModelPayload
        if scope.run.task_type == "question_answer":
            payload = QuestionAnswerModelOutput(
                outcome="answer",
                answer_text=request.answer_text,
                citations=citations,
                risk_hint=request.risk_hint,
                refusal_reason=None,
            )
        elif scope.run.task_type == "summary":
            payload = SummaryModelOutput(
                outcome="answer",
                answer_text=request.answer_text,
                citations=citations,
                risk_hint=request.risk_hint,
                refusal_reason=None,
            )
        else:
            if request.structured_fields is None:
                raise ToolInputInvalid("structured extraction requires fields")
            payload = StructuredExtractionModelOutput(
                outcome="answer",
                answer_text=request.answer_text,
                structured_fields=request.structured_fields,
                citations=citations,
                risk_hint=request.risk_hint,
                refusal_reason=None,
            )
        output = GroundedModelOutput(
            payload=payload,
            identity=ModelIdentity(
                provider=scope.run.model_provider,
                model_name=scope.run.model_name,
                model_version=scope.run.model_version,
            ),
        )
        try:
            answer = validate_grounded_output(
                output,
                request=request_model,
                tenant_id=scope.context.tenant_id,
                document_version_id=scope.context.target_document_version_id,
            )
        except GroundingValidationError as error:
            raise ToolInputInvalid() from error
        if isinstance(answer, GroundedRefusal):
            raise ToolInputInvalid("draft creation does not accept a refusal outcome")
        artifact_payload = {
            "schema_version": 1,
            "run_id": str(scope.run.id),
            "task_type": answer.task_type.value,
            "answer_text": answer.answer_text,
            "structured_fields": answer.structured_fields,
            "risk_hint": answer.risk_hint.value if answer.risk_hint is not None else None,
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
            "behavior_versions": request_model.behavior_versions.model_dump(mode="json"),
        }
        body = json.dumps(
            artifact_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return answer, body

    async def _finalize_draft(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        artifact_id: UUID,
        stored_sha256: str,
        stored_size: int,
        replayed: bool = False,
    ) -> CreateDraftArtifactResult:
        async with self.session_factory.begin() as session:
            artifact = await session.scalar(
                select(AgentArtifact)
                .where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.tenant_id == context.tenant_id,
                    AgentArtifact.run_id == context.run_id,
                )
                .with_for_update()
            )
            if artifact is None:
                raise ToolResultInvalid()
            expected = artifact.content_sha256
            expected_size = artifact.size_bytes
            if (expected is not None and expected != stored_sha256) or (
                expected_size is not None and expected_size != stored_size
            ):
                raise ToolArtifactIntegrityError()
            artifact.content_sha256 = stored_sha256
            artifact.size_bytes = stored_size
            artifact.verified_at = self.clock()
            if artifact.status == AgentArtifactStatus.WRITING.value:
                artifact.status = transition_agent_artifact(
                    AgentArtifactStatus.WRITING,
                    AgentArtifactEvent.MARK_DRAFT_READY,
                ).value
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=context.tenant_id,
            )
            fingerprint = artifact_target_fingerprint(artifact)
            self._mark_execution_succeeded(
                execution,
                result_summary={
                    "artifact_id": str(artifact.id),
                    "content_sha256": stored_sha256,
                    "size_bytes": stored_size,
                    "target_fingerprint": fingerprint,
                },
                now=self.clock(),
            )
            return CreateDraftArtifactResult(
                execution_id=execution.id,
                replayed=replayed,
                artifact_id=artifact.id,
                status=AgentArtifactStatus(artifact.status),
                content_sha256=stored_sha256,
                size_bytes=stored_size,
                target_fingerprint=fingerprint,
            )

    async def _get_artifact_result(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        artifact_id: UUID,
        expires_in_seconds: int,
        replayed: bool,
        mark_success: bool,
    ) -> GetArtifactResult:
        assert self.artifact_store is not None
        async with self.session_factory() as session:
            scope = await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.READ_ARTIFACT,
                artifact_id=artifact_id,
            )
            assert scope.artifact is not None
            if (
                scope.run.publish_requested
                and scope.artifact.status != AgentArtifactStatus.PUBLISHED.value
            ):
                raise ToolPolicyError()
            head = await self.artifact_store.head_object(
                bucket=scope.artifact.object_bucket,
                key=scope.artifact.object_key,
            )
            _verify_head_metadata(scope.artifact, head)
            signed = await self.artifact_store.presign_get(
                bucket=scope.artifact.object_bucket,
                key=scope.artifact.object_key,
                expires_in_seconds=expires_in_seconds,
            )
            result = GetArtifactResult(
                execution_id=execution_id,
                replayed=replayed,
                artifact_id=scope.artifact.id,
                status=AgentArtifactStatus(scope.artifact.status),
                content_sha256=scope.artifact.content_sha256 or "",
                size_bytes=scope.artifact.size_bytes or 0,
                url=signed.url,
                expires_in_seconds=signed.expires_in_seconds,
            )
        if mark_success:
            await self._succeed(
                execution_id=execution_id,
                tenant_id=context.tenant_id,
                result_summary={
                    "artifact_id": str(artifact_id),
                    "content_sha256": result.content_sha256,
                    "size_bytes": result.size_bytes,
                },
            )
        return result

    async def _replay_draft(
        self,
        context: SignedExecutionContext,
        execution_id: UUID,
    ) -> CreateDraftArtifactResult:
        summary = await self._execution_summary(execution_id, context.tenant_id)
        artifact_id = UUID(str(summary["artifact_id"]))
        async with self.session_factory() as session:
            scope = await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.READ_ARTIFACT,
                artifact_id=artifact_id,
            )
            assert scope.artifact is not None
            fingerprint = artifact_target_fingerprint(scope.artifact)
            return CreateDraftArtifactResult(
                execution_id=execution_id,
                replayed=True,
                artifact_id=artifact_id,
                status=AgentArtifactStatus(scope.artifact.status),
                content_sha256=scope.artifact.content_sha256 or "",
                size_bytes=scope.artifact.size_bytes or 0,
                target_fingerprint=fingerprint,
            )

    async def _replay_publish(
        self,
        context: SignedExecutionContext,
        execution_id: UUID,
        artifact_id: UUID,
    ) -> PublishArtifactResult:
        summary = await self._execution_summary(execution_id, context.tenant_id)
        fingerprint = str(summary["target_fingerprint"])
        summary_artifact_id = UUID(str(summary["artifact_id"]))
        if summary_artifact_id != artifact_id:
            raise ToolResultInvalid()
        async with self.session_factory() as session:
            scope = await reload_tool_policy(
                session,
                context=context,
                capability=ToolCapability.PUBLISH,
                artifact_id=artifact_id,
                target_fingerprint=fingerprint,
                allow_succeeded_publish_replay=True,
            )
            assert scope.artifact is not None
            published_at = scope.artifact.published_at
            if published_at is None:
                raise ToolResultInvalid()
            return PublishArtifactResult(
                execution_id=execution_id,
                replayed=True,
                artifact_id=artifact_id,
                status=AgentArtifactStatus(scope.artifact.status),
                target_fingerprint=fingerprint,
                published_at=published_at,
            )

    async def _replay_search(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
    ) -> SearchDocumentResult:
        summary = await self._execution_summary(execution_id, context.tenant_id)
        candidates = await self._frozen_candidates(context)
        refusal = summary.get("refusal_reason")
        return SearchDocumentResult(
            execution_id=execution_id,
            replayed=True,
            accepted=bool(summary.get("accepted", False)),
            refusal_reason=RefusalReason(refusal) if refusal else None,
            candidates=tuple(candidates),
        )

    async def _recover_search(
        self,
        *,
        context: SignedExecutionContext,
        execution_id: UUID,
        expected_started_at: datetime | None,
    ) -> SearchDocumentResult | None:
        summary = await self._execution_summary_or_none(execution_id, context.tenant_id)
        candidates = await self._frozen_candidates(context)
        if summary is None:
            if candidates:
                raise ToolResultInvalid()
            return None
        if candidates or not summary.get("accepted", False):
            await self._succeed(
                execution_id=execution_id,
                tenant_id=context.tenant_id,
                result_summary=summary,
                expected_started_at=expected_started_at,
            )
            return SearchDocumentResult(
                execution_id=execution_id,
                replayed=True,
                accepted=bool(summary.get("accepted", False)),
                refusal_reason=(
                    RefusalReason(summary["refusal_reason"])
                    if summary.get("refusal_reason")
                    else None
                ),
                candidates=tuple(candidates),
            )
        raise ToolExecutionInProgress()

    async def _frozen_candidates(
        self,
        context: SignedExecutionContext,
    ) -> tuple[SearchCandidateResult, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(AgentRunEvidence, DocumentChunk, DocumentVersion)
                    .join(DocumentChunk, DocumentChunk.id == AgentRunEvidence.chunk_id)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == AgentRunEvidence.document_version_id,
                    )
                    .where(
                        AgentRunEvidence.tenant_id == context.tenant_id,
                        AgentRunEvidence.run_id == context.run_id,
                    )
                    .order_by(AgentRunEvidence.rank)
                )
            ).all()
            return tuple(
                SearchCandidateResult(
                    chunk_id=evidence.chunk_id,
                    document_version_id=evidence.document_version_id,
                    generation_id=evidence.generation_id,
                    text=chunk.normalized_text,
                    rank=evidence.rank,
                    score=evidence.rrf_score,
                    page_number=chunk.page_number,
                    heading=chunk.heading,
                    source_filename=version.original_filename,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                )
                for evidence, chunk, version in rows
            )

    async def _execution_summary(self, execution_id: UUID, tenant_id: UUID) -> dict[str, Any]:
        summary = await self._execution_summary_or_none(execution_id, tenant_id)
        if summary is None:
            raise ToolResultInvalid()
        return summary

    async def _execution_summary_or_none(
        self,
        execution_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            execution = await session.scalar(
                select(ToolExecution).where(
                    ToolExecution.id == execution_id,
                    ToolExecution.tenant_id == tenant_id,
                )
            )
            if execution is None:
                raise ToolResultInvalid()
            return dict(execution.result_summary) if execution.result_summary is not None else None

    async def _succeed(
        self,
        *,
        execution_id: UUID,
        tenant_id: UUID,
        result_summary: Mapping[str, Any],
        expected_started_at: datetime | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=tenant_id,
            )
            if expected_started_at is not None and execution.started_at != expected_started_at:
                raise ToolExecutionInProgress()
            self._mark_execution_succeeded(
                execution,
                result_summary=dict(result_summary),
                now=self.clock(),
            )

    async def _fail(
        self,
        execution_id: UUID,
        tenant_id: UUID,
        error_code: str,
        *,
        expected_started_at: datetime | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=tenant_id,
            )
            if expected_started_at is not None and execution.started_at != expected_started_at:
                return
            if execution.status in {
                ToolExecutionStatus.SUCCEEDED.value,
                ToolExecutionStatus.FAILED.value,
                ToolExecutionStatus.DENIED.value,
            }:
                return
            execution.status = transition_tool_execution(
                ToolExecutionStatus.RUNNING,
                ToolExecutionEvent.FAIL,
            ).value
            execution.error_code = error_code
            execution.finished_at = self.clock()

    async def _interrupt_search(
        self,
        execution_id: UUID,
        tenant_id: UUID,
        *,
        expected_started_at: datetime | None,
    ) -> None:
        async with self.session_factory.begin() as session:
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=tenant_id,
            )
            if (
                execution.status != ToolExecutionStatus.RUNNING.value
                or execution.started_at != expected_started_at
            ):
                return
            execution.error_code = _SEARCH_INTERRUPTED_ERROR_CODE

    async def _fail_draft(self, execution_id: UUID, tenant_id: UUID, error_code: str) -> None:
        async with self.session_factory.begin() as session:
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=tenant_id,
            )
            if execution.status in {
                ToolExecutionStatus.SUCCEEDED.value,
                ToolExecutionStatus.FAILED.value,
                ToolExecutionStatus.DENIED.value,
            }:
                return
            if (
                execution.target_resource_type == TargetResourceType.ARTIFACT.value
                and execution.target_resource_id is not None
            ):
                artifact = await session.scalar(
                    select(AgentArtifact)
                    .where(
                        AgentArtifact.id == execution.target_resource_id,
                        AgentArtifact.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if artifact is not None and artifact.status == AgentArtifactStatus.WRITING.value:
                    artifact.status = transition_agent_artifact(
                        AgentArtifactStatus.WRITING,
                        AgentArtifactEvent.FAIL,
                    ).value
            execution.status = transition_tool_execution(
                ToolExecutionStatus(execution.status),
                ToolExecutionEvent.FAIL,
            ).value
            execution.error_code = error_code
            execution.finished_at = self.clock()

    async def _deny(
        self,
        execution_id: UUID,
        tenant_id: UUID,
        error_code: str,
        *,
        expected_started_at: datetime | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            execution = await self._lock_execution(
                session,
                execution_id=execution_id,
                tenant_id=tenant_id,
            )
            if expected_started_at is not None and execution.started_at != expected_started_at:
                return
            if execution.status in {
                ToolExecutionStatus.SUCCEEDED.value,
                ToolExecutionStatus.FAILED.value,
                ToolExecutionStatus.DENIED.value,
            }:
                return
            execution.status = transition_tool_execution(
                ToolExecutionStatus(execution.status),
                ToolExecutionEvent.DENY,
            ).value
            execution.error_code = error_code
            execution.finished_at = self.clock()

    async def _lock_execution(
        self,
        session: AsyncSession,
        *,
        execution_id: UUID,
        tenant_id: UUID,
    ) -> ToolExecution:
        execution = await session.scalar(
            select(ToolExecution)
            .where(ToolExecution.id == execution_id, ToolExecution.tenant_id == tenant_id)
            .with_for_update()
        )
        if execution is None:
            raise ToolResultInvalid()
        return execution

    @staticmethod
    def _mark_execution_succeeded(
        execution: ToolExecution,
        *,
        result_summary: dict[str, Any],
        now: datetime,
    ) -> None:
        if execution.status == ToolExecutionStatus.SUCCEEDED.value:
            return
        execution.status = transition_tool_execution(
            ToolExecutionStatus(execution.status),
            ToolExecutionEvent.SUCCEED,
        ).value
        execution.result_summary = result_summary
        execution.finished_at = now

    async def _verify_object_head(self, artifact: AgentArtifact) -> None:
        assert self.artifact_store is not None
        head = await self.artifact_store.head_object(
            bucket=artifact.object_bucket,
            key=artifact.object_key,
        )
        _verify_head_metadata(artifact, head)

    async def _verify_stored_object(
        self,
        *,
        bucket: str,
        key: str,
        content_sha256: str,
        size_bytes: int,
    ) -> None:
        assert self.artifact_store is not None
        head = await self.artifact_store.head_object(bucket=bucket, key=key)
        if head.size_bytes != size_bytes or head.metadata.get("sha256") != content_sha256:
            raise ToolArtifactIntegrityError()


def _next_lease_started_at(previous: datetime | None, now: datetime) -> datetime:
    if previous is None or now > previous:
        return now
    return previous + timedelta(microseconds=1)


def _input_sha256(request: _ToolModel) -> str:
    encoded = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tool_request_fingerprint(
    *,
    context: SignedExecutionContext,
    tool: ToolName,
    input_sha256: str,
) -> str:
    encoded = json.dumps(
        {
            "approval_request_id": (
                str(context.approval_request_id) if context.approval_request_id else None
            ),
            "capabilities": sorted(capability.value for capability in context.capabilities),
            "execution_id": str(context.execution_id),
            "input_sha256": input_sha256,
            "run_id": str(context.run_id),
            "target_document_version_id": str(context.target_document_version_id),
            "tenant_id": str(context.tenant_id),
            "tool": tool.value,
            "version": context.version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_result(candidate: Any, rank: int) -> SearchCandidateResult:
    return SearchCandidateResult(
        chunk_id=candidate.chunk_id,
        document_version_id=candidate.document_version_id,
        generation_id=candidate.generation_id,
        text=candidate.text,
        rank=rank,
        score=candidate.score,
        page_number=candidate.page_number,
        heading=candidate.heading,
        source_filename=candidate.source_filename,
        start_offset=candidate.start_offset,
        end_offset=candidate.end_offset,
    )


def _verify_head_metadata(artifact: AgentArtifact, head: Any) -> None:
    expected_sha256 = artifact.content_sha256
    expected_size = artifact.size_bytes
    if expected_sha256 is None or expected_size is None:
        raise ToolArtifactIntegrityError()
    if head.size_bytes != expected_size or head.metadata.get("sha256") != expected_sha256:
        raise ToolArtifactIntegrityError()


__all__ = [
    "AgentToolService",
    "CreateDraftArtifactInput",
    "CreateDraftArtifactResult",
    "DraftCitationInput",
    "GetArtifactInput",
    "GetArtifactResult",
    "PublishArtifactInput",
    "PublishArtifactResult",
    "ReadChunkInput",
    "ReadChunkResult",
    "RetrievalService",
    "SearchCandidateResult",
    "SearchDocumentInput",
    "SearchDocumentResult",
    "ToolArtifactIntegrityError",
    "ToolExecutionError",
    "ToolExecutionInProgress",
    "ToolIdempotencyConflict",
    "ToolInputInvalid",
    "ToolName",
    "ToolObjectStoreUnavailable",
    "ToolPriorFailure",
    "ToolResultInvalid",
]
