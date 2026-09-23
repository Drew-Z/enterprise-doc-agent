from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.config import DatabaseSettings, ModelProvider, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import DocumentVersion, HashEmbeddingProvider
from enterprise_doc_core.documents.models import (
    Document,
    DocumentGrant,
    DocumentIngestionGeneration,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesRow
from enterprise_doc_core.presales.schemas import (
    CreatePacket,
    RequirementInput,
    ReviewInput,
    SourceInput,
)
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from tests.agent.test_agent_run_integration import MutableClock, _seed_agent_context
from tests.presales.fixtures import ControlledGateway, add_chunk, add_document

pytestmark = pytest.mark.integration


@pytest.fixture
async def workspace():
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    context = await _seed_agent_context(sessions)
    other = await _seed_agent_context(sessions)
    gateway = ControlledGateway()
    now = datetime.now(UTC).replace(microsecond=0)
    async with sessions.begin() as session:
        session.add(
            TenantEntitlement(
                id=uuid4(),
                tenant_id=context.tenant_id,
                plan_code="integration",
                version=1,
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(hours=1),
                provider_request_limit=100,
                created_at=now,
                updated_at=now,
            )
        )
    usage_service = EntitlementUsageService(session_factory=sessions)
    service = PresalesService(
        session_factory=sessions,
        retriever=HybridRetrievalService(
            session_factory=sessions, embedding_provider=HashEmbeddingProvider()
        ),
        gateway=gateway,
        settings=PresalesSettings(generation_enabled=True),
        usage_service=usage_service,
    )
    try:
        second, generation = await add_document(sessions, context)
        await add_chunk(
            sessions,
            context,
            context.document_version_id,
            context.generation_id,
            "Retention is 30 days.",
        )
        await add_chunk(sessions, context, second, generation, "Retention is 90 days.")
        payload = CreatePacket(
            title="售前保留时间核查",
            sources=[
                SourceInput(version_id=context.document_version_id, applicability="本次采购"),
                SourceInput(version_id=second, applicability="同等效力的备份条款"),
            ],
            requirements=[
                RequirementInput(key="R1", text="Retention", source_location="采购第1条")
            ],
        )
        yield service, sessions, context, other, gateway, payload
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(Tenant).where(Tenant.id.in_([context.tenant_id, other.tenant_id]))
            )
            await session.execute(
                delete(User).where(User.id.in_([context.actor_id, other.actor_id]))
            )
        await engine.dispose()


async def test_multidocument_generate_review_export_and_api_authorization(workspace) -> None:
    service, sessions, context, other, gateway, payload = workspace

    class Resolver:
        async def resolve(self, token: str) -> PrincipalContext:
            return context.principal if token == "owner" else other.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/presales")).status_code == 401
            headers = {"Authorization": "Bearer owner", "Idempotency-Key": "create-1"}
            created = await client.post(
                "/api/presales",
                json=payload.model_dump(mode="json", by_alias=True),
                headers=headers,
            )
            assert created.status_code == 201, created.text
            packet = created.json()
            packet_id, row_id = packet["id"], packet["rows"][0]["id"]
            assert created.headers["cache-control"] == "no-store"
            foreign = await client.get(
                f"/api/presales/{packet_id}", headers={"Authorization": "Bearer other"}
            )
            assert foreign.status_code == 404 and payload.title not in foreign.text
            generated = await client.post(
                f"/api/presales/{packet_id}/rows/{row_id}/generate",
                headers={**headers, "Idempotency-Key": "generate-1"},
            )
            assert generated.status_code == 200, generated.text
            row = generated.json()["rows"][0]
            assert row["state"] == "drafted", row
            assert row["draft"]["status"] == "conflicting_evidence"
            assert len(row["draft"]["citations"]) == 2 and row["review"] is None
            assert (
                await client.get(f"/api/presales/{packet_id}/export?mode=reviewed", headers=headers)
            ).status_code == 409
            review = {
                "expectedRevision": 1,
                "status": "conflicting_evidence",
                "answer": "=公式形式的客户原文\n保留期限需澄清",
                "conditions": [],
                "missingInformation": ["请确认优先条款"],
                "note": "已核对两份资料",
            }
            reviewed = await client.put(
                f"/api/presales/{packet_id}/rows/{row_id}/review",
                json=review,
                headers={**headers, "Idempotency-Key": "review-1"},
            )
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["rows"][0]["draft"] == row["draft"]
            assert reviewed.json()["rows"][0]["review"]["actorId"] == str(context.actor_id)
            replayed = await client.put(
                f"/api/presales/{packet_id}/rows/{row_id}/review",
                json=review,
                headers={**headers, "Idempotency-Key": "review-1"},
            )
            assert len(replayed.json()["rows"][0]["reviewHistory"]) == 1
            exported = await client.get(
                f"/api/presales/{packet_id}/export?mode=reviewed", headers=headers
            )
            assert exported.status_code == 200
            csv_rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
            assert csv_rows[1][5].startswith("'=公式") and "\n" in csv_rows[1][5]
            assert (
                csv_rows[1][10] == "已复核"
                and "30 days" in csv_rows[1][8]
                and "90 days" in csv_rows[1][8]
            )
            assert (
                await client.get(
                    f"/api/presales/{packet_id}/export", headers={"Authorization": "Bearer other"}
                )
            ).status_code == 404
    assert len(gateway.calls) == 1
    async with sessions() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == context.tenant_id,
                    AuditEvent.resource_id == packet_id,
                )
            )
        ).all()
        assert {event.action for event in events} == {
            "presales.packet.created",
            "presales.row.generated",
            "presales.row.reviewed",
            "presales.packet.exported",
        }
        assert all(event.request_id and event.correlation_id for event in events)
        assert all("answer" not in event.event_metadata for event in events)


async def test_concurrent_creation_generation_and_review_do_not_duplicate_or_overwrite(
    workspace,
) -> None:
    service, _, context, _, gateway, payload = workspace
    a, b = await asyncio.gather(
        service.create(context.principal, payload, "same-create"),
        service.create(context.principal, payload, "same-create"),
    )
    assert a.id == b.id
    gateway.release.clear()
    running = asyncio.create_task(service.generate(context.principal, a.id, a.rows[0].id, "first"))
    try:
        await asyncio.wait_for(gateway.entered.wait(), 5)
        replay = await service.generate(context.principal, a.id, a.rows[0].id, "first")
        assert replay.rows[0].state == "running" and len(gateway.calls) == 1
        with pytest.raises(PresalesError, match="presales_generation_busy"):
            await service.generate(context.principal, a.id, a.rows[0].id, "second")
    finally:
        gateway.release.set()
        generated = await running
    draft = generated.rows[0].draft
    assert draft is not None
    review = ReviewInput(
        **draft.model_dump(exclude={"citations", "retrieval"}), expected_revision=1
    )
    results = await asyncio.gather(
        service.review(context.principal, a.id, a.rows[0].id, review, "r-a"),
        service.review(context.principal, a.id, a.rows[0].id, review, "r-b"),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(r, PresalesError) and r.code == "presales_revision_conflict" for r in results
        )
        == 1
    )
    current = await service.get(context.principal, a.id)
    assert len(current.rows[0].review_history) == 1


async def test_timeout_explicit_retry_and_stale_sources(workspace) -> None:
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "create")
    gateway.fail_next = True
    failed = await service.generate(context.principal, packet.id, packet.rows[0].id, "g1")
    assert failed.rows[0].state == "failed" and failed.rows[0].draft is None
    assert failed.rows[0].attempts[0].error_code == "presales_model_timeout"
    await service.generate(context.principal, packet.id, packet.rows[0].id, "g1")
    assert len(gateway.calls) == 1
    retried = await service.generate(context.principal, packet.id, packet.rows[0].id, "g2")
    assert retried.rows[0].state == "drafted" and len(retried.rows[0].attempts) == 2
    async with sessions() as session:
        events = (
            await session.scalars(
                select(UsageEvent).where(
                    UsageEvent.tenant_id == context.tenant_id,
                    UsageEvent.source == "presales",
                )
            )
        ).all()
    assert [event.event_type for event in events].count("release") == 1
    assert [event.event_type for event in events].count("consume") == 1
    async with sessions.begin() as session:
        version = await session.get(DocumentVersion, context.document_version_id)
        version.version_number += 1
    for operation in [
        service.get(context.principal, packet.id),
        service.export(context.principal, packet.id, "draft"),
    ]:
        with pytest.raises(PresalesError, match="presales_stale_sources"):
            await operation
    assert (await service.list_packets(context.principal))[0].stale_sources


async def test_cancelled_generation_releases_reserved_provider_request(workspace) -> None:
    service, sessions, context, _, gateway, payload = workspace

    async def cancel(_payload):
        raise asyncio.CancelledError()

    gateway.generate = cancel
    packet = await service.create(context.principal, payload, "cancel-create")
    with pytest.raises(asyncio.CancelledError):
        await service.generate(context.principal, packet.id, packet.rows[0].id, "cancel")
    async with sessions() as session:
        events = (
            await session.scalars(
                select(UsageEvent).where(
                    UsageEvent.tenant_id == context.tenant_id,
                    UsageEvent.source == "presales",
                )
            )
        ).all()
    assert [event.event_type for event in events] == ["release"]


async def test_revocation_during_generation_discards_output_and_blocks_export(workspace) -> None:
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "create")
    gateway.release.clear()
    running = asyncio.create_task(
        service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    )
    try:
        await asyncio.wait_for(gateway.entered.wait(), 5)
        async with sessions.begin() as session:
            membership = await session.get(Membership, context.membership_id)
            membership.is_active = False
    finally:
        gateway.release.set()
    with pytest.raises(PresalesError, match="presales_forbidden"):
        await running
    async with sessions() as session:
        row = await session.get(PresalesRow, packet.rows[0].id)
        assert row.draft is None
    with pytest.raises(PresalesError, match="presales_forbidden"):
        await service.export(context.principal, packet.id, "draft")


async def test_cross_tenant_source_and_different_payload_replay_are_rejected(workspace) -> None:
    service, _, context, other, _, payload = workspace
    cross = payload.model_copy(
        update={
            "sources": [
                SourceInput(version_id=other.document_version_id, applicability="other tenant")
            ]
        }
    )
    with pytest.raises(PresalesError, match="presales_source_unavailable"):
        await service.create(context.principal, cross, "cross")
    packet = await service.create(context.principal, payload, "same")
    with pytest.raises(PresalesError, match="presales_idempotency_conflict"):
        await service.create(
            context.principal, payload.model_copy(update={"title": "changed"}), "same"
        )
    with pytest.raises(PresalesError, match="presales_not_found"):
        await service.get(other.principal, packet.id)


async def test_expired_execution_cannot_overwrite_explicit_retry(workspace) -> None:
    service, sessions, context, _, old_gateway, payload = workspace
    clock = MutableClock(datetime.now(UTC))
    service.clock = service.generation.clock = clock
    packet = await service.create(context.principal, payload, "create")
    old_gateway.release.clear()
    old = asyncio.create_task(
        service.generate(context.principal, packet.id, packet.rows[0].id, "old")
    )
    try:
        await asyncio.wait_for(old_gateway.entered.wait(), 5)
        pending = await service.get(context.principal, packet.id)
        assert pending.rows[0].attempts[0].provider_request_count is None
        clock.advance(91)
        new_gateway = ControlledGateway()
        service.generation.gateway = new_gateway
        retried = await service.generate(context.principal, packet.id, packet.rows[0].id, "retry")
        assert retried.rows[0].state == "drafted"
        assert [a.state for a in retried.rows[0].attempts] == ["expired", "succeeded"]
        draft = retried.rows[0].draft
    finally:
        old_gateway.release.set()
        await old
    current = await service.get(context.principal, packet.id)
    assert current.rows[0].draft == draft and current.rows[0].revision == 1
    assert [a.provider_request_count for a in current.rows[0].attempts] == [1, 1]
    async with sessions() as session:
        attempts = (
            await session.scalars(
                select(PresalesAttempt).where(PresalesAttempt.row_id == packet.rows[0].id)
            )
        ).all()
        assert sum(a.state == "succeeded" for a in attempts) == 1


async def test_pre_dispatch_failure_is_zero_and_records_behavior_version(workspace) -> None:
    service, _, context, _, gateway, payload = workspace

    async def reject(_payload):
        raise PresalesError("presales_input_too_large")

    gateway.generate = reject
    packet = await service.create(context.principal, payload, "create")
    failed = await service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    attempt = failed.rows[0].attempts[0]
    assert attempt.provider_request_count == 0 and gateway.calls == []
    assert attempt.provenance["pipelineVersion"] == "presales-workspace.v1"
    assert attempt.provenance["promptVersion"] == "controlled-test.v1"


async def test_attempt_and_daily_limits_count_failed_attempts(workspace) -> None:
    service, _, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "first")
    for number in range(3):
        gateway.fail_next = True
        await service.generate(context.principal, packet.id, packet.rows[0].id, f"g{number}")
    with pytest.raises(PresalesError, match="presales_attempt_limit"):
        await service.generate(context.principal, packet.id, packet.rows[0].id, "g4")
    assert len(gateway.calls) == 3
    service.generation.settings = PresalesSettings(generation_enabled=True, daily_attempt_limit=3)
    second = await service.create(context.principal, payload, "second")
    with pytest.raises(PresalesError, match="presales_daily_limit"):
        await service.generate(context.principal, second.id, second.rows[0].id, "daily")
    assert (await service.get(context.principal, second.id)).rows[0].attempts == []


async def test_concurrent_tenant_limit_is_shared_across_packets(workspace) -> None:
    service, _, context, _, gateway, payload = workspace
    service.generation.settings = PresalesSettings(
        generation_enabled=True, concurrent_attempt_limit=1
    )
    first = await service.create(context.principal, payload, "first")
    second = await service.create(context.principal, payload, "second")
    gateway.release.clear()
    running = asyncio.create_task(
        service.generate(context.principal, first.id, first.rows[0].id, "g1")
    )
    try:
        await asyncio.wait_for(gateway.entered.wait(), 5)
        with pytest.raises(PresalesError, match="presales_generation_busy"):
            await service.generate(context.principal, second.id, second.rows[0].id, "g2")
        assert len(gateway.calls) == 1
        assert (await service.get(context.principal, second.id)).rows[0].attempts == []
    finally:
        gateway.release.set()
        await running


async def test_same_tenant_owner_cannot_access_another_authors_sheet(workspace) -> None:
    service, sessions, context, other, _, payload = workspace
    async with sessions.begin() as session:
        session.add(
            Membership(
                tenant_id=context.tenant_id, user_id=other.actor_id, role="owner", is_active=True
            )
        )
    peer = PrincipalContext(
        tenant_id=str(context.tenant_id), actor_id=str(other.actor_id), role="owner"
    )
    packet = await service.create(context.principal, payload, "create")
    for operation in [
        service.get(peer, packet.id),
        service.generate(peer, packet.id, packet.rows[0].id, "peer"),
        service.export(peer, packet.id, "draft"),
    ]:
        with pytest.raises(PresalesError, match="presales_not_found"):
            await operation
    assert await service.list_packets(peer) == []


@pytest.mark.parametrize("during_generation", [True, False])
async def test_document_grant_revocation_blocks_draft_review_and_export(
    workspace, during_generation
) -> None:
    service, sessions, context, other, gateway, payload = workspace
    grant_ids = []
    async with sessions.begin() as session:
        membership = await session.get(Membership, context.membership_id)
        membership.role = "member"
        for source in payload.sources:
            version = await session.get(DocumentVersion, source.version_id)
            document = await session.get(Document, version.document_id)
            document.access_mode, document.created_by = "restricted", other.actor_id
            grant_id = uuid4()
            grant_ids.append(grant_id)
            session.add(
                DocumentGrant(
                    id=grant_id,
                    tenant_id=context.tenant_id,
                    document_id=document.id,
                    grantee_user_id=context.actor_id,
                )
            )
    packet = await service.create(context.principal, payload, "create")
    if during_generation:
        gateway.release.clear()
    running = asyncio.create_task(
        service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    )
    try:
        await asyncio.wait_for(gateway.entered.wait(), 5)
        if not during_generation:
            generated = await running
            draft = generated.rows[0].draft
            await service.review(
                context.principal,
                packet.id,
                packet.rows[0].id,
                ReviewInput(
                    **draft.model_dump(exclude={"citations", "retrieval"}), expected_revision=1
                ),
                "review-before-revocation",
            )
        async with sessions.begin() as session:
            await session.execute(delete(DocumentGrant).where(DocumentGrant.id.in_(grant_ids)))
    finally:
        gateway.release.set()
    if during_generation:
        with pytest.raises(PresalesError, match="presales_source_unavailable"):
            await running
        async with sessions() as session:
            row = await session.get(PresalesRow, packet.rows[0].id)
            assert row.draft is None
    review = ReviewInput(
        status="insufficient_evidence",
        answer="不能证明",
        expected_revision=2,
        missing_information=["补充资料"],
    )
    for operation in [
        service.get(context.principal, packet.id),
        service.review(context.principal, packet.id, packet.rows[0].id, review, "after-revocation"),
        service.export(context.principal, packet.id, "draft"),
        service.export(context.principal, packet.id, "reviewed"),
    ]:
        with pytest.raises(PresalesError, match="presales_source_unavailable"):
            await operation
    assert await service.list_packets(context.principal) == []


async def test_reindexing_invalidates_saved_drafts_and_reviews(workspace) -> None:
    service, sessions, context, _, _, payload = workspace
    packet = await service.create(context.principal, payload, "create")
    await service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    async with sessions.begin() as session:
        old = await session.get(DocumentIngestionGeneration, context.generation_id)
        values = {
            c.key: getattr(old, c.key)
            for c in DocumentIngestionGeneration.__table__.columns
            if c.key not in {"id", "created_at", "updated_at"}
        }
        old.active = False
        values["parser_version"] += 1
        await session.flush()
        session.add(DocumentIngestionGeneration(id=uuid4(), **values))
    with pytest.raises(PresalesError, match="presales_stale_sources"):
        await service.export(context.principal, packet.id, "draft")


@pytest.mark.parametrize("enabled", [False, True])
async def test_disabled_or_unconfigured_generation_creates_no_attempt(workspace, enabled) -> None:
    service, _, context, _, gateway, payload = workspace
    service.generation.settings = PresalesSettings(generation_enabled=enabled)
    if enabled:
        service.generation.gateway = OpenAICompatiblePresalesGateway(ModelSettings())
    packet = await service.create(context.principal, payload, "create")
    code = "presales_model_not_configured" if enabled else "presales_generation_disabled"
    with pytest.raises(PresalesError, match=code):
        await service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    current = await service.get(context.principal, packet.id)
    assert current.rows[0].attempts == [] and current.rows[0].draft is None
    assert gateway.calls == []


@pytest.mark.parametrize("condition", ["expired", "scheduled", "exhausted"])
async def test_commercial_preflight_rejects_generation_without_attempt_or_dispatch(
    workspace, condition
) -> None:
    service, sessions, context, _, gateway, payload = workspace
    usage = service.generation.usage_service
    async with sessions.begin() as session:
        entitlement = await session.scalar(
            select(TenantEntitlement).where(TenantEntitlement.tenant_id == context.tenant_id)
        )
        if condition == "exhausted":
            entitlement.provider_request_limit = 0
        else:
            now = (
                entitlement.period_end
                if condition == "expired"
                else entitlement.period_start - timedelta(microseconds=1)
            )
            usage.clock = lambda: now
    packet = await service.create(context.principal, payload, "create")

    class Resolver:
        async def resolve(self, _token: str) -> PrincipalContext:
            return context.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
        usage_service=usage,
    )
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "generate"}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            rejected = await client.post(
                f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
                headers=headers,
            )
            assert rejected.status_code == (429 if condition == "exhausted" else 403), rejected.text
            assert rejected.json()["error"]["code"] == (
                "presales_usage_limit"
                if condition == "exhausted"
                else "presales_entitlement_inactive"
            )
            assert rejected.json()["error"]["requestId"]
            assert rejected.headers["cache-control"] == "no-store"
            current = await client.get(f"/api/presales/{packet.id}", headers=headers)
            assert current.status_code == 200
            assert current.json()["rows"][0]["attempts"] == []
            assert current.json()["rows"][0]["draft"] is None
            summary = await client.get("/api/tenant-usage", headers=headers)
            assert summary.status_code == 200
            assert summary.json()["entitlementStatus"] == (
                "active" if condition == "exhausted" else "inactive"
            )
            assert summary.json()["providerRequestsRemaining"] == 0
    assert gateway.calls == []
    async with sessions() as session:
        for model in (PresalesAttempt, UsageReservation, UsageEvent):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.tenant_id == context.tenant_id)
                )
                == 0
            )


async def test_expired_entitlement_keeps_draft_read_review_export_and_generation_replay(
    workspace,
) -> None:
    service, sessions, context, _, gateway, payload = workspace
    packet = await service.create(context.principal, payload, "create")
    generated = await service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    draft = generated.rows[0].draft
    assert draft is not None
    usage = service.generation.usage_service
    async with sessions() as session:
        entitlement = await session.scalar(
            select(TenantEntitlement).where(TenantEntitlement.tenant_id == context.tenant_id)
        )
        usage.clock = lambda: entitlement.period_end

    class Resolver:
        async def resolve(self, _token: str) -> PrincipalContext:
            return context.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
        usage_service=usage,
    )
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "generate"}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            summary = await client.get("/api/tenant-usage", headers=headers)
            assert summary.json()["entitlementStatus"] == "inactive"
            current = await client.get(f"/api/presales/{packet.id}", headers=headers)
            assert current.status_code == 200
            replay = await client.post(
                f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
                headers=headers,
            )
            assert replay.status_code == 200
            assert len(replay.json()["rows"][0]["attempts"]) == 1
            reviewed = await client.put(
                f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review",
                headers={**headers, "Idempotency-Key": "review"},
                json=ReviewInput(
                    **draft.model_dump(exclude={"citations", "retrieval"}),
                    expected_revision=1,
                ).model_dump(mode="json", by_alias=True),
            )
            assert reviewed.status_code == 200, reviewed.text
            exported = await client.get(
                f"/api/presales/{packet.id}/export?mode=reviewed",
                headers=headers,
            )
            assert exported.status_code == 200
            assert "已复核" in exported.content.decode("utf-8-sig")
    assert len(gateway.calls) == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.tenant_id == context.tenant_id,
                    UsageEvent.event_type == "consume",
                )
            )
            == 1
        )


@pytest.mark.parametrize("change", ["none", "unknown_reference", "revoked", "stale_source"])
async def test_selection_adapter_preserves_persistence_export_and_authorization(
    workspace, change
) -> None:
    service, sessions, context, other, _, payload = workspace
    calls = []

    async def model_response(request: httpx.Request) -> httpx.Response:
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(sent)
        assert {e["documentVersionId"] for e in sent["evidence"]} == {
            str(s.version_id) for s in payload.sources
        }
        references = [{"citationId": item["citationId"]} for item in sent["evidence"]]
        if change == "unknown_reference":
            references[0]["citationId"] = "foreign-request-reference"
        elif change in {"revoked", "stale_source"}:
            async with sessions.begin() as session:
                if change == "revoked":
                    membership = await session.get(Membership, context.membership_id)
                    membership.is_active = False
                else:
                    version = await session.get(DocumentVersion, context.document_version_id)
                    version.version_number += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "conflicting_evidence",
                                    "answer": "两份条款的保留期限冲突。",
                                    "citations": references,
                                }
                            )
                        },
                    }
                ]
            },
        )

    service.generation.gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://selection.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="selection-integration",
        ),
        transport=httpx.MockTransport(model_response),
    )
    packet = await service.create(context.principal, payload, "selection-create")
    row_id = packet.rows[0].id
    if change in {"revoked", "stale_source"}:
        code = "presales_forbidden" if change == "revoked" else "presales_stale_sources"
        with pytest.raises(PresalesError, match=code):
            await service.generate(context.principal, packet.id, row_id, "selection-generate")
        with pytest.raises(PresalesError, match=code):
            await service.export(context.principal, packet.id, "draft")
        async with sessions() as session:
            row = await session.get(PresalesRow, row_id)
            assert row.draft is None
            attempt = await session.scalar(
                select(PresalesAttempt).where(PresalesAttempt.row_id == row_id)
            )
            assert attempt.state == "failed" and attempt.provider_request_count == 1
    else:
        generated = await service.generate(
            context.principal, packet.id, row_id, "selection-generate"
        )
        row = generated.rows[0]
        assert row.attempts[0].provider_request_count == 1
        assert row.attempts[0].provenance["promptVersion"] == "presales.v3"
        if change == "unknown_reference":
            assert row.draft is None and row.attempts[0].error_code == "presales_invalid_citation"
        else:
            assert row.draft is not None
            assert {c.excerpt for c in row.draft.citations} == {
                "Retention is 30 days.",
                "Retention is 90 days.",
            }
            assert "citationId" not in row.model_dump_json(by_alias=True)
            reviewed = await service.review(
                context.principal,
                packet.id,
                row_id,
                ReviewInput(
                    expected_revision=1,
                    status="conflicting_evidence",
                    answer="已逐字核对。需确认条款优先级。",
                ),
                "selection-review",
            )
            assert reviewed.rows[0].draft == row.draft
            csv_content = (await service.export(context.principal, packet.id, "reviewed")).decode(
                "utf-8-sig"
            )
            assert "Retention is 30 days." in csv_content and "Retention is 90 days." in csv_content
            assert "已逐字核对" in csv_content and "已复核" in csv_content
            with pytest.raises(PresalesError, match="presales_not_found"):
                await service.get(other.principal, packet.id)
        await service.generate(context.principal, packet.id, row_id, "selection-generate")
    assert len(calls) == 1
