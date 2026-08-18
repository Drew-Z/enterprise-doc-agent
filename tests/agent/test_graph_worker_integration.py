from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.agent.test_agent_run_integration import _request, _seed_agent_context, _service

from enterprise_doc_core.agents import (
    AgentArtifact,
    AgentArtifactStatus,
    AgentRun,
    AgentRunEvent,
    AgentRunEvidence,
    AgentRunStatus,
    ApprovalRequest,
    CreateDraftArtifactInput,
    CreateDraftArtifactResult,
    DeterministicGroundedGateway,
    ModelCallTelemetry,
    ModelIdentity,
    SearchCandidateResult,
    SearchDocumentInput,
    SearchDocumentResult,
    SignedExecutionContext,
    ToolCapability,
    artifact_target_fingerprint,
    sign_execution_context,
    verify_execution_context,
)
from enterprise_doc_core.config import DatabaseSettings, McpSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    DEFAULT_EMBEDDING_DIMENSION,
    DocumentChunk,
    DocumentIngestionGeneration,
)
from enterprise_doc_core.jobs import JobRuntimeService, JobStatus
from enterprise_doc_worker.agent_backend import DurableAgentGraphBackend
from enterprise_doc_worker.agent_handler import (
    AgentExecutionContext,
    AgentExecutionPayload,
    SqlAlchemyAgentExecutionLoader,
    build_agent_execution_handler,
)
from enterprise_doc_worker.agents import build_agent_graph_executor
from enterprise_doc_worker.mcp_client import McpStdioClient

pytestmark = pytest.mark.integration


class DatabaseBackedFakeMcpClient(McpStdioClient):
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: McpSettings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def search_document(
        self,
        *,
        context_token: SecretStr | str,
        request: SearchDocumentInput,
    ) -> SearchDocumentResult:
        context = verify_execution_context(
            str(context_token),
            self.settings.signing_secret,
        )
        async with self.session_factory.begin() as session:
            chunk = await session.scalar(
                select(DocumentChunk).where(
                    DocumentChunk.tenant_id == context.tenant_id,
                    DocumentChunk.document_version_id == context.target_document_version_id,
                )
            )
            assert chunk is not None
            existing = await session.scalar(
                select(AgentRunEvidence).where(
                    AgentRunEvidence.run_id == context.run_id,
                    AgentRunEvidence.chunk_id == chunk.id,
                )
            )
            if existing is None:
                session.add(
                    AgentRunEvidence(
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        chunk_id=chunk.id,
                        document_version_id=chunk.document_version_id,
                        generation_id=chunk.generation_id,
                        rank=1,
                        rrf_score=0.9,
                        content_sha256=chunk.content_sha256,
                    )
                )
        return SearchDocumentResult(
            execution_id=uuid4(),
            replayed=False,
            accepted=True,
            refusal_reason=None,
            candidates=(
                SearchCandidateResult(
                    chunk_id=chunk.id,
                    document_version_id=chunk.document_version_id,
                    generation_id=chunk.generation_id,
                    text=chunk.normalized_text,
                    rank=1,
                    score=0.9,
                    page_number=chunk.page_number,
                    heading=chunk.heading,
                    source_filename="contract.txt",
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                ),
            ),
        )

    async def create_draft_artifact(
        self,
        *,
        context_token: SecretStr | str,
        request: CreateDraftArtifactInput,
    ) -> CreateDraftArtifactResult:
        context = verify_execution_context(
            str(context_token),
            self.settings.signing_secret,
        )
        answer_text = request.answer_text
        body = answer_text.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        async with self.session_factory.begin() as session:
            artifact = await session.scalar(
                select(AgentArtifact).where(
                    AgentArtifact.run_id == context.run_id,
                    AgentArtifact.kind == "answer",
                )
            )
            if artifact is None:
                artifact = AgentArtifact(
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    source_document_version_id=context.target_document_version_id,
                    kind="answer",
                    status=AgentArtifactStatus.DRAFT_READY.value,
                    content_type="application/json",
                    object_bucket="artifacts",
                    object_key=f"test/{context.run_id}/answer.json",
                    content_sha256=digest,
                    size_bytes=len(body),
                    behavior_versions={"graph_version": "m4.v1"},
                )
                session.add(artifact)
                await session.flush()
            target_fingerprint = artifact_target_fingerprint(artifact)
        return CreateDraftArtifactResult(
            execution_id=uuid4(),
            replayed=False,
            artifact_id=artifact.id,
            status=AgentArtifactStatus.DRAFT_READY,
            content_sha256=digest,
            size_bytes=len(body),
            target_fingerprint=target_fingerprint,
        )


class TelemetryGateway(DeterministicGroundedGateway):
    async def generate(self, request):
        output = await super().generate(request)
        return replace(
            output,
            identity=ModelIdentity(
                provider="openai_compatible",
                model_name="reviewed-chat-model",
                model_version="2026-08",
                model_revision="revision-1",
            ),
            telemetry=ModelCallTelemetry(
                provider_request_count=2,
                usage_request_count=2,
                prompt_tokens=30,
                completion_tokens=8,
                total_tokens=38,
                repair_request_count=1,
                fallback_count=1,
                breaker_state="open",
                fallback_trigger_code="model_timeout",
            ),
        )


async def test_worker_executes_non_publication_graph_to_terminal_artifact() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    runtime = JobRuntimeService(session_factory=session_factory)
    settings = McpSettings()
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"graph-worker-{uuid4().hex}",
            request=_request(context),
        )
        text = "Payment is due within 30 days after acceptance."
        chunk_id = uuid4()
        async with session_factory.begin() as session:
            generation = await session.get(DocumentIngestionGeneration, context.generation_id)
            assert generation is not None
            generation.chunk_count = 1
            generation.embedded_count = 1
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=context.tenant_id,
                    document_version_id=context.document_version_id,
                    generation_id=context.generation_id,
                    chunk_index=0,
                    heading="Payment",
                    page_number=1,
                    start_offset=0,
                    end_offset=len(text),
                    normalized_text=text,
                    content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    search_vector="'payment':1",
                    embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
                )
            )

        claim = await runtime.claim(job_id=created.job_id, worker_id="graph-worker")
        assert claim is not None
        mcp_client = DatabaseBackedFakeMcpClient(
            session_factory=session_factory,
            settings=settings,
        )

        gateway = TelemetryGateway()

        def backend_factory(
            execution_context: AgentExecutionContext,
        ) -> DurableAgentGraphBackend:
            return DurableAgentGraphBackend(
                session_factory=session_factory,
                context=execution_context,
                gateway=gateway,
                mcp_client=mcp_client,
                mcp_settings=settings,
            )

        executor = build_agent_graph_executor(
            backend_factory=backend_factory,
            gateway=gateway,
            checkpointer=InMemorySaver(),
        )
        handler = build_agent_execution_handler(
            session_factory=session_factory,
            executor=executor,
        )

        await handler(claim)
        assert await runtime.succeed(claim) == JobStatus.SUCCEEDED.value

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            evidence = (
                await session.scalars(
                    select(AgentRunEvidence).where(AgentRunEvidence.run_id == created.run_id)
                )
            ).all()
            artifacts = (
                await session.scalars(
                    select(AgentArtifact).where(AgentArtifact.run_id == created.run_id)
                )
            ).all()
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == created.run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()

        assert run is not None and run.status == AgentRunStatus.SUCCEEDED.value
        assert run.model_provider == "openai_compatible"
        assert run.model_name == "reviewed-chat-model"
        assert run.model_version == "2026-08"
        assert run.model_revision == "revision-1"
        assert run.provider_request_count == 2
        assert run.provider_usage_request_count == 2
        assert run.prompt_tokens == 30
        assert run.completion_tokens == 8
        assert run.total_tokens == 38
        assert run.repair_request_count == 1
        assert run.fallback_count == 1
        assert run.breaker_state == "open"
        assert run.fallback_trigger_code == "model_timeout"
        assert len(evidence) == 1
        assert len(artifacts) == 1
        assert artifacts[0].status == AgentArtifactStatus.DRAFT_READY.value
        assert [event.event_type for event in events] == [
            "run.created",
            "run.started",
            "run.finished",
        ]
    finally:
        await engine.dispose()


async def test_worker_pauses_publication_segment_as_successful_waiting_approval() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    runtime = JobRuntimeService(session_factory=session_factory)
    settings = McpSettings()
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"graph-approval-{uuid4().hex}",
            request=_request(context, publish_requested=True),
        )
        text = "Payment is due within 30 days after acceptance."
        async with session_factory.begin() as session:
            generation = await session.get(DocumentIngestionGeneration, context.generation_id)
            assert generation is not None
            generation.chunk_count = 1
            generation.embedded_count = 1
            session.add(
                DocumentChunk(
                    id=uuid4(),
                    tenant_id=context.tenant_id,
                    document_version_id=context.document_version_id,
                    generation_id=context.generation_id,
                    chunk_index=0,
                    heading="Payment",
                    page_number=1,
                    start_offset=0,
                    end_offset=len(text),
                    normalized_text=text,
                    content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    search_vector="'payment':1",
                    embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
                )
            )

        claim = await runtime.claim(job_id=created.job_id, worker_id="approval-worker")
        assert claim is not None
        gateway = DeterministicGroundedGateway()
        mcp_client = DatabaseBackedFakeMcpClient(
            session_factory=session_factory,
            settings=settings,
        )

        def backend_factory(
            execution_context: AgentExecutionContext,
        ) -> DurableAgentGraphBackend:
            return DurableAgentGraphBackend(
                session_factory=session_factory,
                context=execution_context,
                gateway=gateway,
                mcp_client=mcp_client,
                mcp_settings=settings,
            )

        handler = build_agent_execution_handler(
            session_factory=session_factory,
            executor=build_agent_graph_executor(
                backend_factory=backend_factory,
                gateway=gateway,
                checkpointer=InMemorySaver(),
            ),
        )

        await handler(claim)
        assert await runtime.succeed(claim) == JobStatus.SUCCEEDED.value

        async with session_factory() as session:
            run = await session.get(AgentRun, created.run_id)
            approvals = (
                await session.scalars(
                    select(ApprovalRequest).where(ApprovalRequest.run_id == created.run_id)
                )
            ).all()
            events = (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == created.run_id)
                    .order_by(AgentRunEvent.seq)
                )
            ).all()

        assert run is not None and run.status == AgentRunStatus.WAITING_APPROVAL.value
        assert len(approvals) == 1
        assert [event.event_type for event in events] == [
            "run.created",
            "run.started",
            "run.waiting_approval",
        ]
    finally:
        await engine.dispose()


async def test_real_stdio_client_freezes_evidence_with_fencing_context() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = _service(session_factory)
    runtime = JobRuntimeService(session_factory=session_factory)
    settings = McpSettings()
    try:
        created = await service.create(
            principal=context.principal,
            idempotency_key=f"stdio-search-{uuid4().hex}",
            request=_request(context),
        )
        text = "Payment is due within 30 days after acceptance."
        chunk_id = uuid4()
        async with session_factory.begin() as session:
            generation = await session.get(DocumentIngestionGeneration, context.generation_id)
            assert generation is not None
            generation.embedding_model = "hash-sha256-v1"
            generation.chunk_count = 1
            generation.embedded_count = 1
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=context.tenant_id,
                    document_version_id=context.document_version_id,
                    generation_id=context.generation_id,
                    chunk_index=0,
                    heading="Payment",
                    page_number=1,
                    start_offset=0,
                    end_offset=len(text),
                    normalized_text=text,
                    content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    search_vector="'payment':1",
                    embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
                )
            )

        claim = await runtime.claim(job_id=created.job_id, worker_id="stdio-worker")
        assert claim is not None
        execution = await SqlAlchemyAgentExecutionLoader(session_factory).load(
            claim,
            AgentExecutionPayload.model_validate(claim.payload),
        )
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, execution.run_id)
            assert run is not None
            run.status = AgentRunStatus.RUNNING.value
        now = datetime.now(UTC)
        signed_context = SignedExecutionContext(
            tenant_id=execution.tenant_id,
            actor_id=execution.actor_id,
            run_id=execution.run_id,
            execution_id=execution.execution_id,
            job_id=execution.job_id,
            attempt_id=execution.attempt_id,
            lease_token=execution.lease_token,
            fencing_token=execution.fencing_token,
            capabilities=(ToolCapability.READ_EVIDENCE,),
            target_document_version_id=execution.document_version_id,
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=2),
            nonce=uuid4().hex,
        )
        executable = Path(sys.executable).with_name(
            "enterprise-doc-mcp.exe" if sys.platform == "win32" else "enterprise-doc-mcp"
        )
        client = McpStdioClient(
            command=str(executable),
            request_timeout_seconds=10,
        )

        result = await client.search_document(
            context_token=sign_execution_context(signed_context, settings.signing_secret),
            request=SearchDocumentInput(
                idempotency_key=f"stdio-search:{created.run_id}",
                query="payment",
            ),
        )

        assert result.accepted is True
        assert [candidate.chunk_id for candidate in result.candidates] == [chunk_id]
        async with session_factory() as session:
            evidence = await session.scalar(
                select(AgentRunEvidence).where(
                    AgentRunEvidence.run_id == created.run_id,
                    AgentRunEvidence.chunk_id == chunk_id,
                )
            )
        assert evidence is not None
    finally:
        await engine.dispose()
