from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import (
    SeededAgentContext,
    _request,
    _seed_agent_context,
    _service,
)

from enterprise_doc_core.agents import (
    AgentArtifactNotFound,
    AgentArtifactService,
    AgentRun,
    AgentRunExecution,
    AgentRunNotFound,
    AgentRunStatus,
    SignedExecutionContext,
    ToolCapability,
    ToolPolicyNotFound,
    reload_tool_policy,
)
from enterprise_doc_core.audit import AuditEvent
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import (
    Document,
    DocumentChunk,
    DocumentGrant,
    DocumentGrantInvalid,
    DocumentIngestionGeneration,
    DocumentInventoryService,
    DocumentPolicyService,
    HashEmbeddingProvider,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Membership, Tenant, User

pytestmark = pytest.mark.integration


async def _add_member(
    session_factory: Any,
    *,
    tenant_id: UUID,
    role: str = "member",
) -> UUID:
    actor_id = uuid4()
    suffix = uuid4().hex
    async with session_factory.begin() as session:
        session.add(User(id=actor_id, email=f"acl-{suffix}@example.test"))
        await session.flush()
        session.add(
            Membership(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=actor_id,
                role=role,
                is_active=True,
            )
        )
    return actor_id


def _principal(context: SeededAgentContext, *, actor_id: UUID, role: str) -> PrincipalContext:
    return PrincipalContext(
        tenant_id=str(context.tenant_id),
        actor_id=str(actor_id),
        role=role,
    )


async def test_restricted_document_grants_apply_across_server_boundaries_and_revoke_live() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    member_id = await _add_member(session_factory, tenant_id=context.tenant_id)
    owner_id = await _add_member(session_factory, tenant_id=context.tenant_id, role="owner")
    policy = DocumentPolicyService(session_factory=session_factory)
    inventory = DocumentInventoryService(session_factory=session_factory)
    agent = _service(session_factory)
    retrieval = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=HashEmbeddingProvider(),
    )

    chunk_text = "Payment is due within 30 days after acceptance."
    chunk_id = uuid4()
    try:
        async with session_factory.begin() as session:
            generation = await session.get(
                DocumentIngestionGeneration,
                context.generation_id,
            )
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
                    end_offset=len(chunk_text),
                    normalized_text=chunk_text,
                    content_sha256=hashlib.sha256(chunk_text.encode()).hexdigest(),
                    search_vector="'payment':1",
                    embedding=list((await HashEmbeddingProvider().embed((chunk_text,)))[0]),
                )
            )

        await policy.set_access_mode(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            access_mode="restricted",
            request_id="acl-policy-request",
            correlation_id="acl-policy-correlation",
        )

        assert (
            await inventory.list_versions(
                tenant_id=context.tenant_id,
                actor_id=member_id,
                role="member",
            )
            == ()
        )
        assert (
            await agent.list_ready_document_versions(
                tenant_id=context.tenant_id,
                actor_id=member_id,
            )
            == ()
        )
        denied_retrieval = await retrieval.retrieve(
            tenant_id=context.tenant_id,
            actor_id=member_id,
            document_version_id=context.document_version_id,
            query="payment",
        )
        assert denied_retrieval.accepted is False
        assert denied_retrieval.candidates == ()

        # Creator and a non-creator tenant owner retain access without grants.
        assert (
            len(
                await inventory.list_versions(
                    tenant_id=context.tenant_id,
                    actor_id=context.actor_id,
                    role="member",
                )
            )
            == 1
        )
        assert (
            len(
                await inventory.list_versions(
                    tenant_id=context.tenant_id,
                    actor_id=owner_id,
                    role="owner",
                )
            )
            == 1
        )

        user_grant, concurrent_duplicate = await asyncio.gather(
            policy.add_grant(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                document_id=context.document_id,
                grantee_user_id=member_id,
                request_id="acl-grant-request",
                correlation_id="acl-grant-correlation",
            ),
            policy.add_grant(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                document_id=context.document_id,
                grantee_user_id=member_id,
            ),
        )
        assert concurrent_duplicate.grant_id == user_grant.grant_id
        duplicate = await policy.add_grant(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            grantee_user_id=member_id,
        )
        assert duplicate.grant_id == user_grant.grant_id
        assert (
            len(
                await inventory.list_versions(
                    tenant_id=context.tenant_id,
                    actor_id=member_id,
                    role="member",
                )
            )
            == 1
        )
        assert (
            len(
                await agent.list_ready_document_versions(
                    tenant_id=context.tenant_id,
                    actor_id=member_id,
                )
            )
            == 1
        )
        allowed_retrieval = await retrieval.retrieve(
            tenant_id=context.tenant_id,
            actor_id=member_id,
            document_version_id=context.document_version_id,
            query="payment",
        )
        assert allowed_retrieval.accepted is True
        assert [candidate.chunk_id for candidate in allowed_retrieval.candidates] == [chunk_id]

        created = await agent.create(
            principal=_principal(context, actor_id=member_id, role="member"),
            idempotency_key=f"acl-agent-{uuid4().hex}",
            request=_request(context),
        )
        async with session_factory.begin() as session:
            run = await session.get(AgentRun, created.run_id)
            execution = await session.scalar(
                select(AgentRunExecution).where(AgentRunExecution.run_id == created.run_id)
            )
            assert run is not None and execution is not None
            run.status = AgentRunStatus.RUNNING.value
            run.started_at = datetime.now(UTC)
            execution_id = execution.id
        now = datetime.now(UTC)
        execution_context = SignedExecutionContext(
            tenant_id=context.tenant_id,
            actor_id=member_id,
            run_id=created.run_id,
            execution_id=execution_id,
            capabilities=(ToolCapability.READ_EVIDENCE,),
            target_document_version_id=context.document_version_id,
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            nonce=uuid4().hex,
        )
        async with session_factory() as session:
            await reload_tool_policy(
                session,
                context=execution_context,
                capability=ToolCapability.READ_EVIDENCE,
            )

        await policy.remove_grant(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            grant_id=user_grant.grant_id,
            request_id="acl-revoke-request",
            correlation_id="acl-revoke-correlation",
        )
        await policy.remove_grant(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            grant_id=user_grant.grant_id,
        )

        async with session_factory() as session:
            with pytest.raises(ToolPolicyNotFound):
                await reload_tool_policy(
                    session,
                    context=execution_context,
                    capability=ToolCapability.READ_EVIDENCE,
                )
        with pytest.raises(AgentRunNotFound):
            await agent.get_status(
                run_id=created.run_id,
                tenant_id=context.tenant_id,
                actor_id=member_id,
            )
        with pytest.raises(AgentRunNotFound):
            await agent.list_events(
                run_id=created.run_id,
                tenant_id=context.tenant_id,
                actor_id=member_id,
            )
        artifact_service = AgentArtifactService(
            session_factory=session_factory,
            artifact_store=cast(Any, object()),
        )
        with pytest.raises(AgentArtifactNotFound):
            await artifact_service.list_for_run(
                tenant_id=context.tenant_id,
                actor_id=member_id,
                run_id=created.run_id,
            )

        role_grant = await policy.add_grant(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            grantee_role="member",
        )
        assert (
            len(
                await inventory.list_versions(
                    tenant_id=context.tenant_id,
                    actor_id=member_id,
                    role="member",
                )
            )
            == 1
        )
        await policy.remove_grant(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            document_id=context.document_id,
            grant_id=role_grant.grant_id,
        )

        async with session_factory() as session:
            audit_actions = set(
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.resource_type == "document",
                        AuditEvent.resource_id == context.document_id,
                    )
                )
            )
            stored_grants = tuple(
                await session.scalars(
                    select(DocumentGrant).where(DocumentGrant.document_id == context.document_id)
                )
            )
        assert {
            "document.policy.updated",
            "document.grant.added",
            "document.grant.removed",
        } <= audit_actions
        assert stored_grants == ()
    finally:
        await engine.dispose()


async def test_cross_tenant_user_cannot_be_granted_document_access() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    first = await _seed_agent_context(session_factory)
    second = await _seed_agent_context(session_factory)
    policy = DocumentPolicyService(session_factory=session_factory)
    try:
        with pytest.raises(DocumentGrantInvalid):
            await policy.add_grant(
                tenant_id=first.tenant_id,
                actor_id=first.actor_id,
                role="owner",
                document_id=first.document_id,
                grantee_user_id=second.actor_id,
            )
        async with session_factory() as session:
            document = await session.get(Document, first.document_id)
            other_tenant = await session.get(Tenant, second.tenant_id)
        assert document is not None and other_tenant is not None
    finally:
        await engine.dispose()
