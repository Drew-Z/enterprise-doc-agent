from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tests.agent.test_agent_run_integration import (
    SeededAgentContext,
    _request,
    _seed_agent_context,
    _service,
)

from enterprise_doc_core.agents import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentGraphError,
    AgentRun,
    AgentRunEvent,
    AgentRunExecution,
    AgentRunStatus,
    AgentToolService,
    ApprovalAlreadyDecided,
    ApprovalDecision,
    ApprovalPrincipalForbidden,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalService,
    ApprovalTargetMismatch,
    CreateDraftArtifactInput,
    CreateDraftArtifactResult,
    DecideApprovalInput,
    DeterministicGroundedGateway,
    PublishArtifactInput,
    SearchDocumentInput,
    SearchDocumentResult,
    SignedExecutionContext,
    artifact_target_fingerprint,
    verify_execution_context,
)
from enterprise_doc_core.config import DatabaseSettings, McpSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.retrieval import RetrievalCandidate, RetrievalDecision
from enterprise_doc_core.identity import Membership
from enterprise_doc_core.jobs import Job, JobRuntimeService, JobStatus, OutboxEvent
from enterprise_doc_core.object_store import (
    ArtifactObject,
    ObjectHead,
    PresignedObjectDownload,
)
from enterprise_doc_worker.agent_backend import DurableAgentGraphBackend
from enterprise_doc_worker.agent_handler import (
    AgentExecutionContext,
    AgentExecutionPayload,
    AgentExecutionRuntimeError,
    SqlAlchemyAgentExecutionLoader,
    build_agent_execution_handler,
)
from enterprise_doc_worker.agents import build_agent_graph_executor
from enterprise_doc_worker.mcp_client import McpStdioClient

pytestmark = pytest.mark.integration


class FixedRetrieval:
    def __init__(self, candidate: RetrievalCandidate) -> None:
        self.candidate = candidate

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision:
        assert tenant_id == self.candidate.tenant_id
        assert document_version_id == self.candidate.document_version_id
        assert query
        return RetrievalDecision(True, (self.candidate,))


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str, dict[str, str]]] = {}

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> ArtifactObject:
        digest = hashlib.sha256(body).hexdigest()
        self.objects[(bucket, key)] = (body, content_type, {"sha256": digest, **metadata})
        return ArtifactObject(
            bucket=bucket,
            key=key,
            size_bytes=len(body),
            content_sha256=digest,
            content_type=content_type,
            etag='"memory"',
        )

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        body, content_type, metadata = self.objects[(bucket, key)]
        return ObjectHead(
            size_bytes=len(body),
            etag='"memory"',
            checksum_sha256_b64=None,
            content_type=content_type,
            metadata=metadata,
        )

    async def delete_object(self, *, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)

    async def presign_get(
        self,
        *,
        bucket: str,
        key: str,
        expires_in_seconds: int,
    ) -> PresignedObjectDownload:
        assert (bucket, key) in self.objects
        return PresignedObjectDownload(
            url=f"https://download.test/{key}",
            expires_in_seconds=expires_in_seconds,
        )

    async def close(self) -> None:
        return None


class ToolBackedMcpClient(McpStdioClient):
    def __init__(self, *, service: AgentToolService, settings: McpSettings) -> None:
        self.tool_service = service
        self.settings = settings

    def _context(self, token: SecretStr | str) -> SignedExecutionContext:
        return verify_execution_context(str(token), self.settings.signing_secret)

    async def search_document(
        self,
        *,
        context_token: SecretStr | str,
        request: SearchDocumentInput,
    ) -> SearchDocumentResult:
        return await self.tool_service.search_document(self._context(context_token), request)

    async def create_draft_artifact(
        self,
        *,
        context_token: SecretStr | str,
        request: CreateDraftArtifactInput,
    ) -> CreateDraftArtifactResult:
        return await self.tool_service.create_draft_artifact(self._context(context_token), request)

    async def publish_artifact(
        self,
        *,
        context_token: SecretStr | str,
        request: PublishArtifactInput,
    ) -> Any:
        return await self.tool_service.publish_artifact(self._context(context_token), request)


@dataclass(slots=True)
class PreparedRun:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    seeded: SeededAgentContext
    created_run_id: UUID
    service: Any
    runtime: JobRuntimeService
    handler: Any
    saver: InMemorySaver
    settings: McpSettings
    backend_factory: Any
    approval_id: UUID
    artifact_id: UUID
    target_fingerprint: str


async def _prepare_waiting_run() -> PreparedRun:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    seeded = await _seed_agent_context(session_factory)
    run_service = _service(session_factory)
    runtime = JobRuntimeService(session_factory=session_factory)
    settings = McpSettings()
    created = await run_service.create(
        principal=seeded.principal,
        idempotency_key=f"approval-integration:{uuid4().hex}",
        request=_request(seeded, publish_requested=True),
    )

    text = "Payment is due within 30 days after acceptance."
    chunk_id = uuid4()
    async with session_factory.begin() as session:
        generation = await session.get(DocumentIngestionGeneration, seeded.generation_id)
        assert generation is not None
        generation.chunk_count = 1
        generation.embedded_count = 1
        session.add(
            DocumentChunk(
                id=chunk_id,
                tenant_id=seeded.tenant_id,
                document_version_id=seeded.document_version_id,
                generation_id=seeded.generation_id,
                chunk_index=0,
                heading="Payment",
                page_number=1,
                start_offset=0,
                end_offset=len(text),
                normalized_text=text,
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                search_vector="'payment':1",
                embedding=[0.1] * 8,
            )
        )

    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        tenant_id=seeded.tenant_id,
        document_version_id=seeded.document_version_id,
        generation_id=seeded.generation_id,
        text=text,
        page_number=1,
        heading="Payment",
        start_offset=0,
        end_offset=len(text),
        source_filename=seeded.filename,
        score=0.9,
    )
    tool_service = AgentToolService(
        session_factory=session_factory,
        retrieval_service=FixedRetrieval(candidate),
        artifact_store=MemoryArtifactStore(),
    )
    mcp_client = ToolBackedMcpClient(service=tool_service, settings=settings)
    gateway = DeterministicGroundedGateway()
    saver = InMemorySaver()

    def backend_factory(context: AgentExecutionContext) -> DurableAgentGraphBackend:
        return DurableAgentGraphBackend(
            session_factory=session_factory,
            context=context,
            gateway=gateway,
            mcp_client=mcp_client,
            mcp_settings=settings,
        )

    handler = build_agent_execution_handler(
        session_factory=session_factory,
        executor=build_agent_graph_executor(
            backend_factory=backend_factory,
            gateway=gateway,
            checkpointer=saver,
        ),
    )
    claim = await runtime.claim(job_id=created.job_id, worker_id=f"approval-{uuid4().hex}")
    assert claim is not None
    await handler(claim)
    assert await runtime.succeed(claim) == JobStatus.SUCCEEDED.value

    async with session_factory() as session:
        approval = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == created.run_id)
        )
        artifact = await session.scalar(
            select(AgentArtifact).where(AgentArtifact.run_id == created.run_id)
        )
    assert approval is not None and artifact is not None
    return PreparedRun(
        engine=engine,
        session_factory=session_factory,
        seeded=seeded,
        created_run_id=created.run_id,
        service=run_service,
        runtime=runtime,
        handler=handler,
        saver=saver,
        settings=settings,
        backend_factory=backend_factory,
        approval_id=approval.id,
        artifact_id=artifact.id,
        target_fingerprint=artifact_target_fingerprint(artifact),
    )


def _decision_input(prepared: PreparedRun, decision: str = "approved") -> DecideApprovalInput:
    return DecideApprovalInput(
        decision=decision,
        operation="publish_artifact",
        target_resource_type="agent_artifact",
        target_resource_id=prepared.artifact_id,
        target_document_version_id=prepared.seeded.document_version_id,
        target_fingerprint=prepared.target_fingerprint,
        comment="Reviewed in integration test",
    )


def _fixed_clock(value: datetime) -> Any:
    def clock() -> datetime:
        return value

    return clock


async def _decide(
    prepared: PreparedRun,
    *,
    decision: str = "approved",
    key: str | None = None,
    clock: Any | None = None,
) -> Any:
    if clock is None:
        clock = _fixed_clock(datetime.now(UTC))
    service = ApprovalService(
        session_factory=prepared.session_factory,
        clock=clock,
    )
    return await service.decide(
        tenant_id=prepared.seeded.tenant_id,
        actor_id=prepared.seeded.actor_id,
        approval_id=prepared.approval_id,
        idempotency_key=key or f"decision:{uuid4().hex}",
        request=_decision_input(prepared, decision),
    )


@pytest.mark.asyncio
async def test_approved_decision_is_atomic_idempotent_and_resumes_same_thread() -> None:
    prepared = await _prepare_waiting_run()
    try:
        key = f"approved:{uuid4().hex}"
        first = await _decide(prepared, key=key)
        replay = await _decide(prepared, key=key)
        assert first.decision == ApprovalDecision.APPROVED.value
        assert first.replayed is False
        assert replay.replayed is True
        assert replay.decision == ApprovalDecision.APPROVED.value
        assert replay.resume_job_id == first.resume_job_id

        claim = await prepared.runtime.claim(
            job_id=first.resume_job_id,
            worker_id=f"resume-{uuid4().hex}",
        )
        assert claim is not None
        await prepared.handler(claim)
        assert await prepared.runtime.succeed(claim) == JobStatus.SUCCEEDED.value

        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            approval = await session.get(ApprovalRequest, prepared.approval_id)
            artifact = await session.get(AgentArtifact, prepared.artifact_id)
            executions = (
                await session.scalars(
                    select(AgentRunExecution).where(
                        AgentRunExecution.run_id == prepared.created_run_id
                    )
                )
            ).all()
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == prepared.created_run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()
            jobs = (await session.scalars(select(Job).where(Job.id == first.resume_job_id))).all()
            outbox = (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == first.resume_job_id)
                )
            ).all()
        assert run is not None and run.status == AgentRunStatus.SUCCEEDED.value
        assert approval is not None and approval.status == ApprovalRequestStatus.CONSUMED.value
        assert artifact is not None and artifact.status == AgentArtifactStatus.PUBLISHED.value
        assert len(executions) == 2
        assert len(jobs) == 1 and len(outbox) == 1
        assert [event.event_type for event in events].count("run.finished") == 1
        assert run.graph_thread_id == str(prepared.created_run_id)
        consumed_replay = await _decide(prepared, key=key)
        assert consumed_replay.replayed is True
        assert consumed_replay.status == ApprovalRequestStatus.CONSUMED.value
        assert consumed_replay.decision == ApprovalDecision.APPROVED.value
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["rejected", "expired"])
async def test_rejected_and_expired_decisions_resume_to_terminal_without_duplicates(
    decision: str,
) -> None:
    prepared = await _prepare_waiting_run()
    try:
        clock = None
        if decision == "expired":
            async with prepared.session_factory() as session:
                approval = await session.get(ApprovalRequest, prepared.approval_id)
                assert approval is not None
                expiry = approval.expires_at + timedelta(seconds=1)
            clock = _fixed_clock(expiry)
        result = await _decide(prepared, decision=decision, clock=clock)
        assert result.decision == decision
        runtime = (
            prepared.runtime
            if clock is None
            else JobRuntimeService(session_factory=prepared.session_factory, clock=clock)
        )
        claim = await runtime.claim(
            job_id=result.resume_job_id,
            worker_id=f"terminal-{uuid4().hex}",
        )
        assert claim is not None
        await prepared.handler(claim)
        assert await runtime.succeed(claim) == JobStatus.SUCCEEDED.value
        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == prepared.created_run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()
        assert run is not None and run.status == decision
        assert [event.event_type for event in events].count("run.finished") == 1
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_rejection_converges_even_when_target_changes_after_request() -> None:
    prepared = await _prepare_waiting_run()
    try:
        async with prepared.session_factory.begin() as session:
            artifact = await session.get(AgentArtifact, prepared.artifact_id)
            assert artifact is not None
            artifact.content_sha256 = "f" * 64

        result = await _decide(prepared, decision="rejected")
        claim = await prepared.runtime.claim(
            job_id=result.resume_job_id,
            worker_id=f"reject-changed-{uuid4().hex}",
        )
        assert claim is not None
        await prepared.handler(claim)
        assert await prepared.runtime.succeed(claim) == JobStatus.SUCCEEDED.value

        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            approval = await session.get(ApprovalRequest, prepared.approval_id)
        assert run is not None and run.status == AgentRunStatus.REJECTED.value
        assert approval is not None and approval.status == ApprovalRequestStatus.REJECTED.value
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_expired_replay_keeps_original_request_fingerprint() -> None:
    prepared = await _prepare_waiting_run()
    try:
        async with prepared.session_factory() as session:
            approval = await session.get(ApprovalRequest, prepared.approval_id)
            assert approval is not None
            expiry = approval.expires_at + timedelta(seconds=1)
        key = f"expired:{uuid4().hex}"
        first = await _decide(prepared, key=key, clock=_fixed_clock(expiry))
        replay = await _decide(prepared, key=key, clock=_fixed_clock(expiry))
        assert first.decision == replay.decision == ApprovalDecision.EXPIRED.value
        assert first.decision_fingerprint == replay.decision_fingerprint
        assert replay.replayed is True
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_owner_target_concurrency_revoke_and_cancel_boundaries() -> None:
    prepared = await _prepare_waiting_run()
    try:
        # Exact target mismatch is rejected before any state mutation.
        bad = _decision_input(prepared)
        bad = DecideApprovalInput(
            decision=bad.decision,
            operation=bad.operation,
            target_resource_type=bad.target_resource_type,
            target_resource_id=bad.target_resource_id,
            target_document_version_id=bad.target_document_version_id,
            target_fingerprint="b" * 64,
            comment=bad.comment,
        )
        service = ApprovalService(session_factory=prepared.session_factory)
        with pytest.raises(ApprovalTargetMismatch):
            await service.decide(
                tenant_id=prepared.seeded.tenant_id,
                actor_id=prepared.seeded.actor_id,
                approval_id=prepared.approval_id,
                idempotency_key=f"bad:{uuid4().hex}",
                request=bad,
            )
        async with prepared.session_factory.begin() as session:
            membership = await session.get(Membership, prepared.seeded.membership_id)
            assert membership is not None
            membership.is_active = False
        with pytest.raises(ApprovalPrincipalForbidden):
            await _decide(prepared)
        async with prepared.session_factory.begin() as session:
            membership = await session.get(Membership, prepared.seeded.membership_id)
            assert membership is not None
            membership.is_active = True

        approved = await _decide(prepared, key=f"concurrent:{uuid4().hex}")
        service = ApprovalService(session_factory=prepared.session_factory)
        revoke = await service.revoke(
            tenant_id=prepared.seeded.tenant_id,
            actor_id=prepared.seeded.actor_id,
            approval_id=prepared.approval_id,
        )
        assert revoke.changed is True
        async with prepared.session_factory() as session:
            approval = await session.get(ApprovalRequest, prepared.approval_id)
            job = await session.get(Job, approved.resume_job_id)
            run = await session.get(AgentRun, prepared.created_run_id)
        assert approval is not None and approval.status == ApprovalRequestStatus.REVOKED.value
        assert job is not None and job.status == JobStatus.CANCELLED.value
        assert run is not None and run.status == AgentRunStatus.CANCELLED.value

        # A run cancellation revokes the current approval and cancels its Job.
        prepared2 = await _prepare_waiting_run()
        try:
            approved2 = await _decide(prepared2)
            cancelled = await prepared2.service.cancel(
                run_id=prepared2.created_run_id,
                tenant_id=prepared2.seeded.tenant_id,
                actor_id=prepared2.seeded.actor_id,
            )
            assert cancelled.status == AgentRunStatus.CANCELLED.value
            async with prepared2.session_factory() as session:
                approval2 = await session.get(ApprovalRequest, prepared2.approval_id)
                job2 = await session.get(Job, approved2.resume_job_id)
            assert approval2 is not None and approval2.status == ApprovalRequestStatus.REVOKED.value
            assert job2 is not None and job2.status == JobStatus.CANCELLED.value
        finally:
            await prepared2.engine.dispose()
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_revoke_cancels_run_after_resume_job_has_started() -> None:
    prepared = await _prepare_waiting_run()
    try:
        approved = await _decide(prepared)
        claim = await prepared.runtime.claim(
            job_id=approved.resume_job_id,
            worker_id=f"started-resume-{uuid4().hex}",
        )
        assert claim is not None
        payload = AgentExecutionPayload.model_validate(claim.payload)
        context = await SqlAlchemyAgentExecutionLoader(prepared.session_factory).load(
            claim,
            payload,
        )
        backend = prepared.backend_factory(context)
        await backend.prepare_segment()

        service = ApprovalService(session_factory=prepared.session_factory)
        revoked = await service.revoke(
            tenant_id=prepared.seeded.tenant_id,
            actor_id=prepared.seeded.actor_id,
            approval_id=prepared.approval_id,
        )
        assert revoked.changed is True
        assert revoked.resume_job_cancelled is True

        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            job = await session.get(Job, approved.resume_job_id)
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == prepared.created_run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()
        assert run is not None and run.status == AgentRunStatus.CANCELLED.value
        assert run.cancelled_at is not None and run.finished_at is not None
        assert job is not None and job.status == JobStatus.RUNNING.value
        assert job.cancel_requested_at is not None
        assert [event.event_type for event in events][-2:] == [
            "run.resumed",
            "run.cancelled",
        ]
        assert [event.event_type for event in events].count("run.cancelled") == 1
        with pytest.raises(AgentGraphError, match="stale"):
            await backend.prepare_segment()
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["membership", "version"])
async def test_resume_rechecks_authorization_and_document_version_before_publish(
    mutation: str,
) -> None:
    prepared = await _prepare_waiting_run()
    try:
        approved = await _decide(prepared)
        claim = await prepared.runtime.claim(
            job_id=approved.resume_job_id,
            worker_id=f"stale-resume-{uuid4().hex}",
        )
        assert claim is not None
        if mutation == "membership":
            async with prepared.session_factory.begin() as session:
                membership = await session.get(Membership, prepared.seeded.membership_id)
                assert membership is not None
                membership.is_active = False
        else:
            async with prepared.session_factory.begin() as session:
                version = await session.get(DocumentVersion, prepared.seeded.document_version_id)
                assert version is not None
                version.status = DocumentVersionStatus.UPLOADED.value
        with pytest.raises(AgentExecutionRuntimeError):
            await prepared.handler(claim)
        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            approval = await session.get(ApprovalRequest, prepared.approval_id)
            artifact = await session.get(AgentArtifact, prepared.artifact_id)
        assert run is not None and run.status == AgentRunStatus.WAITING_APPROVAL.value
        assert approval is not None and approval.status == ApprovalRequestStatus.APPROVED.value
        assert artifact is not None and artifact.status == AgentArtifactStatus.DRAFT_READY.value
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_decisions_have_one_effective_winner() -> None:
    prepared = await _prepare_waiting_run()
    try:
        service = ApprovalService(session_factory=prepared.session_factory)
        request = _decision_input(prepared)
        rejected = DecideApprovalInput(
            decision="rejected",
            operation=request.operation,
            target_resource_type=request.target_resource_type,
            target_resource_id=request.target_resource_id,
            target_document_version_id=request.target_document_version_id,
            target_fingerprint=request.target_fingerprint,
            comment=request.comment,
        )
        results = await asyncio.wait_for(
            asyncio.gather(
                service.decide(
                    tenant_id=prepared.seeded.tenant_id,
                    actor_id=prepared.seeded.actor_id,
                    approval_id=prepared.approval_id,
                    idempotency_key=f"winner-a:{uuid4().hex}",
                    request=request,
                ),
                service.decide(
                    tenant_id=prepared.seeded.tenant_id,
                    actor_id=prepared.seeded.actor_id,
                    approval_id=prepared.approval_id,
                    idempotency_key=f"winner-b:{uuid4().hex}",
                    request=rejected,
                ),
                return_exceptions=True,
            ),
            timeout=15,
        )
        successful = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(successful) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ApprovalAlreadyDecided)
        async with prepared.session_factory() as session:
            executions = (
                await session.scalars(
                    select(AgentRunExecution).where(
                        AgentRunExecution.approval_request_id == prepared.approval_id
                    )
                )
            ).all()
        assert len(executions) == 1
    finally:
        await prepared.engine.dispose()


@pytest.mark.asyncio
async def test_decision_and_run_cancel_race_has_one_terminal_outcome() -> None:
    prepared = await _prepare_waiting_run()
    try:
        approval_service = ApprovalService(session_factory=prepared.session_factory)
        decision_task = asyncio.create_task(
            approval_service.decide(
                tenant_id=prepared.seeded.tenant_id,
                actor_id=prepared.seeded.actor_id,
                approval_id=prepared.approval_id,
                idempotency_key=f"race:{uuid4().hex}",
                request=_decision_input(prepared),
            )
        )
        cancel_task = asyncio.create_task(
            prepared.service.cancel(
                run_id=prepared.created_run_id,
                tenant_id=prepared.seeded.tenant_id,
                actor_id=prepared.seeded.actor_id,
            )
        )
        results = await asyncio.wait_for(
            asyncio.gather(decision_task, cancel_task, return_exceptions=True),
            timeout=15,
        )
        assert not any(
            isinstance(result, (TimeoutError, asyncio.TimeoutError)) for result in results
        )
        async with prepared.session_factory() as session:
            run = await session.get(AgentRun, prepared.created_run_id)
            approval = await session.get(ApprovalRequest, prepared.approval_id)
            executions = (
                await session.scalars(
                    select(AgentRunExecution).where(
                        AgentRunExecution.approval_request_id == prepared.approval_id
                    )
                )
            ).all()
        assert run is not None and run.status == AgentRunStatus.CANCELLED.value
        assert approval is not None and approval.status == ApprovalRequestStatus.REVOKED.value
        assert len(executions) <= 1
        assert any(not isinstance(result, Exception) for result in results)
    finally:
        await prepared.engine.dispose()
