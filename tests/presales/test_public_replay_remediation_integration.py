import httpx
import pytest
from sqlalchemy import select

from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.models import PresalesAttempt
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.jobs.test_m3_retrieval_integration import (
    VECTOR_A,
    VECTOR_B,
    ControlledEmbeddingProvider,
    _add_generation,
    _seed_identity,
    _seed_version,
)
from tests.presales.test_presales_background_integration import background as background
from tests.presales.test_presales_background_integration import (
    configured_worker,
    enqueue,
    valid_response,
)

pytestmark = pytest.mark.integration


async def test_chinese_requirement_recalls_clause_without_vector_help_and_keeps_scope(browser_db):
    sessions = browser_db.sessions
    tenant, actor = await _seed_identity(sessions)
    version = await _seed_version(
        sessions, tenant_id=tenant, actor_id=actor, filename="procurement.txt"
    )
    clause = "提供原厂制造商盖章软件授权证书及售后服务承诺函\uff0c保障原厂升级服务和售后服务。"
    _, target = await _add_generation(sessions, version=version, text=clause, embedding=VECTOR_B)
    # Identical content in an inactive generation or another tenant must never be offered.
    await _add_generation(
        sessions,
        version=version,
        text=clause,
        embedding=VECTOR_B,
        embedding_version=2,
        active=False,
    )
    other_tenant, other_actor = await _seed_identity(sessions)
    other = await _seed_version(
        sessions, tenant_id=other_tenant, actor_id=other_actor, filename="private.txt"
    )
    await _add_generation(sessions, version=other, text=clause, embedding=VECTOR_B)
    query = "提交原厂盖章的软件授权证书及售后服务承诺函。"
    service = HybridRetrievalService(
        session_factory=sessions,
        embedding_provider=ControlledEmbeddingProvider({query: VECTOR_A}),
        embedding_model="controlled",
    )
    result = await service.retrieve(
        tenant_id=tenant,
        actor_id=actor,
        document_version_id=version.document_version_id,
        query=query,
    )
    assert result.accepted
    assert [candidate.chunk_id for candidate in result.candidates] == [target]
    assert clause == result.candidates[0].text
    forbidden = await service.retrieve(
        tenant_id=other_tenant,
        actor_id=other_actor,
        document_version_id=version.document_version_id,
        query=query,
    )
    assert not forbidden.accepted and not forbidden.candidates


async def test_sync_error_and_background_recovery_both_keep_fixed_diagnostics(background):
    b = background
    b.settings.background_generation_enabled = False

    def invalid(request):
        b.requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": "sensitive-partial"}}
                ]
            },
        )

    b.client.handler = invalid
    packet = await b.service.create(b.context.principal, b.payload, "sync-diagnostic-create")
    result = await b.service.generate(
        b.context.principal, packet.id, packet.rows[0].id, "sync-once"
    )
    assert result.rows[0].attempts[0].provenance["providerCall1Diagnostic"] == "incomplete_output"
    b.settings.background_generation_enabled = True
    calls = []

    def recover(request):
        calls.append(request.url.host)
        return (
            invalid(request) if request.url.host == "primary.invalid" else valid_response(request)
        )

    worker = configured_worker(b, recover)
    queued = await enqueue(b, "recover-diagnostic")
    assert await worker.run_once("recover-diagnostic")
    restored = await b.service.get(b.context.principal, queued.id)
    assert restored.rows[0].state == "drafted"
    assert restored.rows[0].attempts[0].provenance["providerCall1Diagnostic"] == "incomplete_output"
    assert calls == ["primary.invalid", "fallback.invalid"]
    assert "sensitive-partial" not in restored.model_dump_json()


async def test_background_rejection_keeps_safe_diagnostic_on_refresh_without_redispatch(background):
    b = background

    def invalid(request):
        b.requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "failure-one",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "private-provider-body"}}
                ],
                "usage": {"total_tokens": 17},
            },
        )

    b.client.handler = invalid
    packet = await b.service.create(b.context.principal, b.payload, "diagnostic-create")
    row = packet.rows[0]
    await b.service.generate(b.context.principal, packet.id, row.id, "diagnostic-once")
    worker = BackgroundGeneration(b.service.generation, {"primary": b.service.generation.gateway})
    assert await worker.run_once("diagnostic-worker")
    refreshed = await b.service.get(b.context.principal, packet.id)
    attempt = refreshed.rows[0].attempts[0]
    assert attempt.error_code == "presales_invalid_model_output"
    assert attempt.provenance["providerCall1Diagnostic"] == "draft_json"
    assert "private-provider-body" not in refreshed.model_dump_json()
    await b.service.generate(b.context.principal, packet.id, row.id, "diagnostic-once")
    assert not await worker.run_once("diagnostic-worker")
    assert len(b.requests) == 1
    async with b.sessions() as session:
        stored = await session.scalar(
            select(PresalesAttempt).where(PresalesAttempt.row_id == row.id)
        )
        assert stored.provider_request_count == 1
        assert stored.provenance["providerCall1Diagnostic"] == "draft_json"
