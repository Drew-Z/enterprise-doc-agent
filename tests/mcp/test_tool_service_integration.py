from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents import (
    AgentArtifact,
    AgentRun,
    AgentRunExecution,
    AgentRunStatus,
    AgentToolService,
    ApprovalRequest,
    ApprovalRequestStatus,
    CreateDraftArtifactInput,
    GetArtifactInput,
    PublishArtifactInput,
    ReadChunkInput,
    SearchDocumentInput,
    SignedExecutionContext,
    ToolApprovalError,
    ToolCapability,
    ToolExecution,
    ToolObjectStoreUnavailable,
    ToolPolicyNotFound,
)
from enterprise_doc_core.agents.models import AgentRunExecutionKind
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    DEFAULT_EMBEDDING_DIMENSION,
    DocumentChunk,
    DocumentIngestionGeneration,
)
from enterprise_doc_core.documents.retrieval import RetrievalCandidate, RetrievalDecision
from enterprise_doc_core.identity import Membership
from enterprise_doc_core.jobs import create_job_records
from enterprise_doc_core.object_store import (
    ArtifactObject,
    ObjectHead,
    PresignedObjectDownload,
)
from tests.agent.test_agent_run_integration import (
    _request,
    _seed_agent_context,
    _service,
)

pytestmark = pytest.mark.integration


class FixedRetrieval:
    def __init__(self, candidate: RetrievalCandidate) -> None:
        self.candidate = candidate
        self.calls = 0

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision:
        self.calls += 1
        assert tenant_id == self.candidate.tenant_id
        assert document_version_id == self.candidate.document_version_id
        assert query
        return RetrievalDecision(True, (self.candidate,))


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str, dict[str, str]]] = {}
        self.put_calls = 0

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> ArtifactObject:
        self.put_calls += 1
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


class CancelAfterPutArtifactStore(MemoryArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_once = True

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> ArtifactObject:
        result = await super().put_object(
            bucket=bucket,
            key=key,
            body=body,
            content_type=content_type,
            metadata=metadata,
        )
        if self.cancel_once:
            self.cancel_once = False
            raise asyncio.CancelledError()
        return result


def _context(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    run_id: UUID,
    execution_id: UUID,
    document_version_id: UUID,
    capabilities: tuple[ToolCapability, ...] | None = None,
    approval_request_id: UUID | None = None,
) -> SignedExecutionContext:
    now = datetime.now(UTC)
    return SignedExecutionContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        run_id=run_id,
        execution_id=execution_id,
        capabilities=capabilities
        or (
            ToolCapability.READ_EVIDENCE,
            ToolCapability.CREATE_DRAFT,
            ToolCapability.READ_ARTIFACT,
        ),
        target_document_version_id=document_version_id,
        approval_request_id=approval_request_id,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        nonce=uuid4().hex,
    )


async def _seed_running_tool_run(
    session_factory: async_sessionmaker[AsyncSession],
    context_seed: Any,
    *,
    idempotency_key: str,
    publish_requested: bool = False,
) -> tuple[object, UUID, RetrievalCandidate, str]:
    """Create one running run with a real evidence chunk for tool integration tests."""
    agent_service = _service(session_factory)
    run_result = await agent_service.create(
        principal=context_seed.principal,
        idempotency_key=idempotency_key,
        request=_request(context_seed, publish_requested=publish_requested),
    )
    chunk_id = uuid4()
    chunk_text = "Payment is due within 30 days after acceptance."
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        tenant_id=context_seed.tenant_id,
        document_version_id=context_seed.document_version_id,
        generation_id=context_seed.generation_id,
        text=chunk_text,
        page_number=1,
        heading="Payment",
        start_offset=0,
        end_offset=len(chunk_text),
        source_filename=context_seed.filename,
        score=0.9,
    )
    async with session_factory.begin() as session:
        run = await session.get(AgentRun, run_result.run_id)
        execution = await session.scalar(
            select(AgentRunExecution).where(AgentRunExecution.run_id == run_result.run_id)
        )
        generation = await session.get(DocumentIngestionGeneration, context_seed.generation_id)
        assert run is not None and execution is not None and generation is not None
        run.status = AgentRunStatus.RUNNING.value
        generation.chunk_count = 1
        generation.embedded_count = 1
        session.add(
            DocumentChunk(
                id=chunk_id,
                tenant_id=context_seed.tenant_id,
                document_version_id=context_seed.document_version_id,
                generation_id=context_seed.generation_id,
                chunk_index=0,
                heading="Payment",
                page_number=1,
                start_offset=0,
                end_offset=len(chunk_text),
                normalized_text=chunk_text,
                content_sha256=hashlib.sha256(chunk_text.encode()).hexdigest(),
                search_vector="'payment':1",
                embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
            )
        )
        execution_id = execution.id
    return run_result, execution_id, candidate, chunk_text


async def test_tool_service_freezes_evidence_and_replays_artifact_reads() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context_seed = await _seed_agent_context(session_factory)
    agent_service = _service(session_factory)
    run_result = await agent_service.create(
        principal=context_seed.principal,
        idempotency_key=f"tool-run-{uuid4().hex}",
        request=_request(context_seed),
    )
    chunk_id = uuid4()
    text = "Payment is due within 30 days after acceptance."
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        tenant_id=context_seed.tenant_id,
        document_version_id=context_seed.document_version_id,
        generation_id=context_seed.generation_id,
        text=text,
        page_number=1,
        heading="Payment",
        start_offset=0,
        end_offset=len(text),
        source_filename=context_seed.filename,
        score=0.9,
    )
    try:
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, run_result.run_id)
            execution = await session.scalar(
                select(AgentRunExecution).where(AgentRunExecution.run_id == run_result.run_id)
            )
            generation = await session.get(DocumentIngestionGeneration, context_seed.generation_id)
            assert run is not None and execution is not None and generation is not None
            run.status = AgentRunStatus.RUNNING.value
            generation.chunk_count = 1
            generation.embedded_count = 1
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=context_seed.tenant_id,
                    document_version_id=context_seed.document_version_id,
                    generation_id=context_seed.generation_id,
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
            execution_id = execution.id

        context = _context(
            tenant_id=context_seed.tenant_id,
            actor_id=context_seed.actor_id,
            run_id=run_result.run_id,
            execution_id=execution_id,
            document_version_id=context_seed.document_version_id,
        )
        retrieval = FixedRetrieval(candidate)
        store = MemoryArtifactStore()
        service = AgentToolService(
            session_factory=session_factory,
            retrieval_service=retrieval,
            artifact_store=store,
        )

        searched = await service.search_document(
            context,
            SearchDocumentInput(idempotency_key="search-1", query="payment terms"),
        )
        assert searched.accepted is True
        assert searched.candidates[0].chunk_id == chunk_id
        assert retrieval.calls == 1
        replayed_search = await service.search_document(
            context,
            SearchDocumentInput(idempotency_key="search-1", query="payment terms"),
        )
        assert replayed_search.replayed is True
        assert retrieval.calls == 1

        read = await service.read_chunk(
            context,
            ReadChunkInput(idempotency_key="read-1", chunk_id=chunk_id),
        )
        assert read.text == text

        draft = await service.create_draft_artifact(
            context,
            CreateDraftArtifactInput(
                idempotency_key="draft-1",
                answer_text="Payment is due within 30 days after acceptance.",
                citations=[
                    {
                        "chunk_id": chunk_id,
                        "document_version_id": context_seed.document_version_id,
                        "excerpt": "Payment is due within 30 days",
                    }
                ],
            ),
        )
        assert draft.status.value == "draft_ready"
        assert draft.size_bytes > 0

        download = await service.get_artifact(
            context,
            GetArtifactInput(idempotency_key="get-1", artifact_id=draft.artifact_id),
        )
        assert download.url.startswith("https://download.test/")
        replayed_download = await service.get_artifact(
            context,
            GetArtifactInput(idempotency_key="get-1", artifact_id=draft.artifact_id),
        )
        assert replayed_download.replayed is True
    finally:
        await engine.dispose()


async def test_create_draft_recovers_after_finalize_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context_seed = await _seed_agent_context(session_factory)
    current = [datetime.now(UTC)]
    run_result, execution_id, candidate, chunk_text = await _seed_running_tool_run(
        session_factory,
        context_seed,
        idempotency_key=f"draft-recovery-run-{uuid4().hex}",
    )
    context = _context(
        tenant_id=context_seed.tenant_id,
        actor_id=context_seed.actor_id,
        run_id=run_result.run_id,
        execution_id=execution_id,
        document_version_id=context_seed.document_version_id,
    )
    store = MemoryArtifactStore()
    service = AgentToolService(
        session_factory=session_factory,
        retrieval_service=FixedRetrieval(candidate),
        artifact_store=store,
        clock=lambda: current[0],
        stale_execution_seconds=1,
    )
    request = CreateDraftArtifactInput(
        idempotency_key="draft-recovery",
        answer_text=chunk_text,
        citations=[
            {
                "chunk_id": candidate.chunk_id,
                "document_version_id": context_seed.document_version_id,
                "excerpt": "Payment is due within 30 days",
            }
        ],
    )
    try:
        await service.search_document(
            context,
            SearchDocumentInput(idempotency_key="draft-recovery-search", query="payment"),
        )
        original_finalize = service._finalize_draft
        failed_once = True

        async def fail_finalize_once(*args: Any, **kwargs: Any) -> Any:
            nonlocal failed_once
            if failed_once:
                failed_once = False
                raise RuntimeError("simulated finalize outage")
            return await original_finalize(*args, **kwargs)

        monkeypatch.setattr(service, "_finalize_draft", fail_finalize_once)
        with pytest.raises(ToolObjectStoreUnavailable):
            await service.create_draft_artifact(context, request)
        monkeypatch.setattr(service, "_finalize_draft", original_finalize)

        async with session_factory() as session:
            execution = await session.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == context_seed.tenant_id,
                    ToolExecution.idempotency_key == request.idempotency_key,
                )
            )
            artifact = await session.scalar(
                select(AgentArtifact).where(AgentArtifact.run_id == run_result.run_id)
            )
            assert execution is not None and artifact is not None
            assert execution.status == "running"
            assert artifact.status == "writing"
            assert artifact.content_sha256 is not None
            assert artifact.size_bytes is not None

        current[0] += timedelta(seconds=2)
        recovered = await service.create_draft_artifact(context, request)
        assert recovered.replayed is True
        assert recovered.status.value == "draft_ready"
        assert store.put_calls == 2
        assert len(store.objects) == 1
        async with session_factory() as session:
            execution = await session.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == context_seed.tenant_id,
                    ToolExecution.idempotency_key == request.idempotency_key,
                )
            )
            artifact = await session.scalar(
                select(AgentArtifact).where(AgentArtifact.run_id == run_result.run_id)
            )
            assert execution is not None and artifact is not None
            assert execution.status == "succeeded"
            assert artifact.status == "draft_ready"
    finally:
        await engine.dispose()


async def test_create_draft_recovers_after_cancellation_following_object_put() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context_seed = await _seed_agent_context(session_factory)
    current = [datetime.now(UTC)]
    run_result, execution_id, candidate, chunk_text = await _seed_running_tool_run(
        session_factory,
        context_seed,
        idempotency_key=f"draft-cancel-run-{uuid4().hex}",
    )
    context = _context(
        tenant_id=context_seed.tenant_id,
        actor_id=context_seed.actor_id,
        run_id=run_result.run_id,
        execution_id=execution_id,
        document_version_id=context_seed.document_version_id,
    )
    store = CancelAfterPutArtifactStore()
    service = AgentToolService(
        session_factory=session_factory,
        retrieval_service=FixedRetrieval(candidate),
        artifact_store=store,
        clock=lambda: current[0],
        stale_execution_seconds=1,
    )
    request = CreateDraftArtifactInput(
        idempotency_key="draft-cancel-recovery",
        answer_text=chunk_text,
        citations=[
            {
                "chunk_id": candidate.chunk_id,
                "document_version_id": context_seed.document_version_id,
                "excerpt": "Payment is due within 30 days",
            }
        ],
    )
    try:
        await service.search_document(
            context,
            SearchDocumentInput(idempotency_key="draft-cancel-search", query="payment"),
        )
        with pytest.raises(asyncio.CancelledError):
            await service.create_draft_artifact(context, request)

        async with session_factory() as session:
            execution = await session.scalar(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == context_seed.tenant_id,
                    ToolExecution.idempotency_key == "draft-cancel-recovery",
                )
            )
            artifact = await session.scalar(
                select(AgentArtifact).where(AgentArtifact.run_id == run_result.run_id)
            )
            assert execution is not None and artifact is not None
            assert execution.status == "running"
            assert artifact.status == "writing"

        current[0] += timedelta(seconds=2)
        recovered = await service.create_draft_artifact(context, request)
        assert recovered.replayed is True
        assert recovered.status.value == "draft_ready"
        assert store.put_calls == 2
        assert len(store.objects) == 1
    finally:
        await engine.dispose()


async def test_publish_requires_owner_exact_approval_and_replays_once() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context_seed = await _seed_agent_context(session_factory)
    agent_service = _service(session_factory)
    run_result = await agent_service.create(
        principal=context_seed.principal,
        idempotency_key=f"publish-run-{uuid4().hex}",
        request=_request(context_seed, publish_requested=True),
    )
    chunk_id = uuid4()
    chunk_text = "Payment is due within 30 days after acceptance."
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        tenant_id=context_seed.tenant_id,
        document_version_id=context_seed.document_version_id,
        generation_id=context_seed.generation_id,
        text=chunk_text,
        page_number=1,
        heading="Payment",
        start_offset=0,
        end_offset=len(chunk_text),
        source_filename=context_seed.filename,
        score=0.9,
    )
    try:
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, run_result.run_id)
            execution = await session.scalar(
                select(AgentRunExecution).where(AgentRunExecution.run_id == run_result.run_id)
            )
            generation = await session.get(
                DocumentIngestionGeneration,
                context_seed.generation_id,
            )
            assert run is not None and execution is not None and generation is not None
            run.status = AgentRunStatus.RUNNING.value
            generation.chunk_count = 1
            generation.embedded_count = 1
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=context_seed.tenant_id,
                    document_version_id=context_seed.document_version_id,
                    generation_id=context_seed.generation_id,
                    chunk_index=0,
                    heading="Payment",
                    page_number=1,
                    start_offset=0,
                    end_offset=len(chunk_text),
                    normalized_text=chunk_text,
                    content_sha256=hashlib.sha256(chunk_text.encode()).hexdigest(),
                    search_vector="'payment':1",
                    embedding=[0.1] * DEFAULT_EMBEDDING_DIMENSION,
                )
            )
            execution_id = execution.id

        initial_context = _context(
            tenant_id=context_seed.tenant_id,
            actor_id=context_seed.actor_id,
            run_id=run_result.run_id,
            execution_id=execution_id,
            document_version_id=context_seed.document_version_id,
        )
        store = MemoryArtifactStore()
        service = AgentToolService(
            session_factory=session_factory,
            retrieval_service=FixedRetrieval(candidate),
            artifact_store=store,
        )
        await service.search_document(
            initial_context,
            SearchDocumentInput(idempotency_key="publish-search", query="payment"),
        )
        draft = await service.create_draft_artifact(
            initial_context,
            CreateDraftArtifactInput(
                idempotency_key="publish-draft",
                answer_text=chunk_text,
                citations=[
                    {
                        "chunk_id": chunk_id,
                        "document_version_id": context_seed.document_version_id,
                        "excerpt": "Payment is due within 30 days",
                    }
                ],
            ),
        )
        approval_id = uuid4()
        now = datetime.now(UTC)
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, run_result.run_id)
            assert run is not None
            run.status = AgentRunStatus.WAITING_APPROVAL.value
            session.add(
                ApprovalRequest(
                    id=approval_id,
                    tenant_id=context_seed.tenant_id,
                    run_id=run_result.run_id,
                    requested_by_actor_id=context_seed.actor_id,
                    decided_by_actor_id=context_seed.actor_id,
                    operation="publish_artifact",
                    target_resource_type="agent_artifact",
                    target_resource_id=draft.artifact_id,
                    target_document_version_id=context_seed.document_version_id,
                    target_fingerprint=draft.target_fingerprint,
                    status=ApprovalRequestStatus.APPROVED.value,
                    requested_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(minutes=10),
                    decided_at=now,
                )
            )
            membership = await session.get(Membership, context_seed.membership_id)
            assert membership is not None
            membership.role = "member"
            await session.flush()
            resume_job = await create_job_records(
                session,
                tenant_id=context_seed.tenant_id,
                actor_id=context_seed.actor_id,
                job_type="agent.execute",
                idempotency_key=f"agent:{run_result.run_id}:execution:1",
                payload={
                    "payload_version": 1,
                    "run_id": str(run_result.run_id),
                    "execution_sequence": 1,
                    "graph_thread_id": str(run_result.run_id),
                    "graph_version": run.graph_version,
                },
                request_id=None,
                correlation_id=None,
                outbox_event_type=None,
            )
            run.current_execution_seq = 1
            run.status = AgentRunStatus.RUNNING.value
            resume_execution = AgentRunExecution(
                tenant_id=context_seed.tenant_id,
                run_id=run_result.run_id,
                sequence=1,
                job_id=resume_job.job_id,
                kind=AgentRunExecutionKind.RESUME.value,
                approval_request_id=approval_id,
                resume_fingerprint="r" * 64,
            )
            session.add(resume_execution)
            await session.flush()
            resume_execution_id = resume_execution.id

        with pytest.raises(ToolPolicyNotFound):
            await service.read_chunk(
                initial_context,
                ReadChunkInput(idempotency_key="stale-initial-context", chunk_id=chunk_id),
            )

        publish_context = _context(
            tenant_id=context_seed.tenant_id,
            actor_id=context_seed.actor_id,
            run_id=run_result.run_id,
            execution_id=resume_execution_id,
            document_version_id=context_seed.document_version_id,
            capabilities=(ToolCapability.PUBLISH,),
            approval_request_id=approval_id,
        )
        with pytest.raises(ToolApprovalError):
            await service.publish_artifact(
                publish_context,
                PublishArtifactInput(
                    idempotency_key="publish-denied",
                    artifact_id=draft.artifact_id,
                    target_fingerprint=draft.target_fingerprint,
                ),
            )
        async with session_factory.begin() as session:
            artifact = await session.get(AgentArtifact, draft.artifact_id)
            approval = await session.get(ApprovalRequest, approval_id)
            membership = await session.get(Membership, context_seed.membership_id)
            assert artifact is not None and approval is not None and membership is not None
            assert artifact.status == "draft_ready"
            assert approval.status == "approved"
            membership.role = "owner"

        published = await service.publish_artifact(
            publish_context,
            PublishArtifactInput(
                idempotency_key="publish-once",
                artifact_id=draft.artifact_id,
                target_fingerprint=draft.target_fingerprint,
            ),
        )
        replayed = await service.publish_artifact(
            publish_context,
            PublishArtifactInput(
                idempotency_key="publish-once",
                artifact_id=draft.artifact_id,
                target_fingerprint=draft.target_fingerprint,
            ),
        )

        assert published.status.value == "published"
        assert replayed.replayed is True
        assert replayed.published_at == published.published_at

        async with session_factory.begin() as session:
            run = await session.get(AgentRun, run_result.run_id)
            assert run is not None
            run.status = AgentRunStatus.SUCCEEDED.value

        replayed_after_success = await service.publish_artifact(
            publish_context,
            PublishArtifactInput(
                idempotency_key="publish-once",
                artifact_id=draft.artifact_id,
                target_fingerprint=draft.target_fingerprint,
            ),
        )
        assert replayed_after_success.replayed is True
        assert replayed_after_success.published_at == published.published_at
        with pytest.raises(ToolPolicyNotFound):
            await service.publish_artifact(
                publish_context,
                PublishArtifactInput(
                    idempotency_key="publish-after-success-new-key",
                    artifact_id=draft.artifact_id,
                    target_fingerprint=draft.target_fingerprint,
                ),
            )
        assert len(store.objects) == 1
        async with session_factory() as session:
            artifact = await session.get(AgentArtifact, draft.artifact_id)
            approval = await session.get(ApprovalRequest, approval_id)
            executions = (
                await session.scalars(
                    select(ToolExecution).where(
                        ToolExecution.tenant_id == context_seed.tenant_id,
                        ToolExecution.tool_name == "publish_artifact",
                    )
                )
            ).all()
            assert artifact is not None and approval is not None
            assert artifact.status == "published"
            assert approval.status == "consumed"
            assert {execution.status for execution in executions} == {"denied", "succeeded"}
    finally:
        await engine.dispose()
