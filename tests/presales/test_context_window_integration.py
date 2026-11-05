import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select

from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity.models import Membership
from enterprise_doc_core.presales.access import source_snapshots
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.context_window import expand_context
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import RequirementInput, SourceInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.jobs.test_m3_retrieval_integration import VECTOR_A, VECTOR_B, ControlledEmbeddingProvider
from tests.presales.fixtures import add_document
from tests.presales.test_presales_background_integration import background as background
from tests.presales.test_question_context_integration import CASES, seed_source

pytestmark = pytest.mark.integration
QUESTION = "primaryneedle secondaryneedle"


async def seed_chunks(b, version=None, generation=None, *, width=1200):
    version = version or b.context.document_version_id
    generation = generation or b.context.generation_id
    texts = [
        "primaryneedle original first hit. ",
        "NEIGHBOR: The required encryption algorithm is AES-256. ",
        "secondaryneedle original second hit. ",
        "Nearby but less preferred context. ",
        "Farther context. ",
    ]
    candidates = []
    async with b.sessions.begin() as session:
        await session.execute(
            delete(DocumentChunk).where(DocumentChunk.generation_id == generation)
        )
        filename = (await session.get(DocumentVersion, version)).original_filename
        for index, text in enumerate(texts):
            text += "x" * (width - len(text))
            chunk_id = uuid4()
            start = index * width
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    tenant_id=b.context.tenant_id,
                    document_version_id=version,
                    generation_id=generation,
                    chunk_index=index,
                    start_offset=start,
                    end_offset=start + len(text),
                    normalized_text=text,
                    content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    search_vector=func.to_tsvector("simple", text),
                    embedding=list(VECTOR_A if index in (0, 2) else VECTOR_B),
                )
            )
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk_id,
                    tenant_id=b.context.tenant_id,
                    document_version_id=version,
                    generation_id=generation,
                    text=text,
                    start_offset=start,
                    end_offset=start + len(text),
                    source_filename=filename,
                )
            )
        row = await session.get(DocumentIngestionGeneration, generation)
        row.chunk_count = row.embedded_count = len(candidates)
    async with b.sessions() as session:
        source = (
            await source_snapshots(
                session,
                b.context.tenant_id,
                b.context.actor_id,
                [SourceInput(version_id=version, applicability="current public fixture")],
            )
        )[0]
    embeddings = ControlledEmbeddingProvider({QUESTION: VECTOR_A})
    b.service.generation.retriever = HybridRetrievalService(
        session_factory=b.sessions,
        embedding_provider=embeddings,
    )
    return source, candidates, embeddings


async def expand(b, source, seeds, slots=10):
    return await expand_context(
        b.sessions,
        tenant_id=b.context.tenant_id,
        actor_id=b.context.actor_id,
        source=source,
        seeds=seeds,
        extra_slots=slots,
    )


async def test_actual_postgres_window_binds_original_chunks_and_filters_other_sources(background):
    b = background
    source, chunks, _ = await seed_chunks(b)
    second, generation = await add_document(b.sessions, b.context)
    _, foreign, _ = await seed_chunks(b, second, generation)
    result = await expand(b, source, (chunks[0], chunks[2]))
    assert result.candidates == (chunks[0], chunks[2], chunks[1])
    assert result.truncated
    assert not {c.chunk_id for c in result.candidates} & {c.chunk_id for c in foreign}
    assert sum(len(c.text) for c in result.candidates) == 3600


async def test_oversized_neighbor_is_skipped_whole_and_reported(background):
    b = background
    source, chunks, _ = await seed_chunks(b)
    async with b.sessions.begin() as session:
        neighbor = await session.get(DocumentChunk, chunks[1].chunk_id)
        neighbor.normalized_text += "z" * 1800
        neighbor.end_offset += 1800
        neighbor.content_sha256 = hashlib.sha256(neighbor.normalized_text.encode()).hexdigest()
    result = await expand(b, source, (chunks[0],))
    assert chunks[1].chunk_id not in {c.chunk_id for c in result.candidates}
    assert chunks[1].chunk_id in result.available_ids
    assert result.truncated
    assert result.candidates[0] == chunks[0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["original_id"])
async def test_known_public_sources_keep_v25_hits_with_real_sql_and_bounded_context(
    background, case
):
    b = background
    await seed_source(b, case)
    packet = await b.service.create(b.context.principal, b.payload, "known-context")
    requirement = RequirementInput(key=case["original_id"], text=case["question"])
    original, _ = await b.service.generation._retrieve(
        b.context.tenant_id,
        b.context.actor_id,
        requirement,
        packet.sources,
        complementary_evidence=True,
    )
    expanded, notes = await b.service.generation._retrieve(
        b.context.tenant_id,
        b.context.actor_id,
        requirement,
        packet.sources,
        complementary_evidence=True,
        context_window=True,
    )
    assert expanded[: len(original)] == original
    assert sum(len(c.text) for c in expanded) <= 3600
    assert len(expanded) <= 12
    assert any(case["required_excerpt"] in c.text for c in expanded)
    assert all(c.text == case["source_text"][c.start_offset : c.end_offset] for c in expanded)
    assert notes[0].used_count == len(expanded)


@pytest.mark.parametrize("change", ["tenant", "version", "generation", "text", "offset", "deleted"])
async def test_stale_or_forged_seed_fails_before_context_is_offered(background, change):
    b = background
    source, chunks, _ = await seed_chunks(b)
    seed = chunks[0]
    if change == "deleted":
        async with b.sessions.begin() as session:
            await session.execute(delete(DocumentChunk).where(DocumentChunk.id == seed.chunk_id))
    else:
        field, value = {
            "tenant": ("tenant_id", b.other.tenant_id),
            "version": ("document_version_id", uuid4()),
            "generation": ("generation_id", uuid4()),
            "text": ("text", "forged source text"),
            "offset": ("start_offset", 1),
        }[change]
        seed = replace(seed, **{field: value})
    with pytest.raises(PresalesError, match="presales_invalid_evidence"):
        await expand(b, source, (seed, chunks[2]))


@pytest.mark.parametrize(
    "change", ["revoked", "inactive_generation", "failed_version", "snapshot_changed"]
)
async def test_source_access_and_active_snapshot_rechecked(background, change):
    b = background
    source, chunks, _ = await seed_chunks(b)
    async with b.sessions.begin() as session:
        if change == "revoked":
            row = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == b.context.tenant_id,
                    Membership.user_id == b.context.actor_id,
                )
            )
            row.is_active = False
        elif change == "inactive_generation":
            (await session.get(DocumentIngestionGeneration, source.generation_id)).active = False
        elif change == "failed_version":
            (await session.get(DocumentVersion, source.version_id)).status = "failed"
        else:
            (
                await session.get(DocumentVersion, source.version_id)
            ).original_filename = "changed.txt"
    with pytest.raises(PresalesError, match=r"presales_source_unavailable|presales_stale_sources"):
        await expand(b, source, (chunks[0], chunks[2]))


@pytest.mark.parametrize("source_count", [5, 6])
async def test_source_seeds_reserved_before_global_context_budget(background, source_count):
    b = background
    source, chunks, _ = await seed_chunks(b)
    sources, all_seeds = [source], [chunks[0], chunks[2]]
    for _ in range(source_count - 1):
        version, generation = await add_document(b.sessions, b.context)
        source, chunks, _ = await seed_chunks(b, version, generation)
        sources.append(source)
        all_seeds.extend([chunks[0], chunks[2]])
    result, notes = await b.service.generation._retrieve(
        b.context.tenant_id,
        b.context.actor_id,
        RequirementInput(key="Q", text=QUESTION),
        sources,
        complementary_evidence=True,
        context_window=True,
    )
    assert len(result) == 12
    assert {s.chunk_id for s in all_seeds} <= {c.chunk_id for c in result}
    assert all(
        sum(len(c.text) for c in result if c.document_version_id == s.version_id) <= 3600
        for s in sources
    )
    assert sum(note.used_count for note in notes) == 12


@pytest.mark.parametrize("background_mode", [False, True])
@pytest.mark.parametrize("version", ["presales.v24", "presales.v25", "presales.v26"])
async def test_actual_shared_pipeline_persists_neighbor_citation_and_accounting(
    background, background_mode, version
):
    b = background
    source, chunks, _ = await seed_chunks(b)
    calls = []

    def respond(request):
        body = json.loads(request.content)
        wire = json.loads(body["messages"][1]["content"])
        calls.append(wire)
        neighbor = next((e for e in wire["evidence"] if e["text"].startswith("NEIGHBOR:")), None)
        assert (neighbor is not None) == (version == "presales.v26")
        assert any(e["text"].startswith("primaryneedle") for e in wire["evidence"])
        assert any(e["text"].startswith("secondaryneedle") for e in wire["evidence"])
        evidence = neighbor or wire["evidence"][0]
        answer = {
            "rules": [],
            "assessments": [],
            "status": "supported",
            "conclusion": "资料已明确说明。",
            "responses": [
                {
                    "requirementPartId": wire["requirementParts"][0]["requirementPartId"],
                    "answer": "已依据授权原文填写。",
                    "citations": [{"citationId": evidence["citationId"]}],
                    "missingInformation": [],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}],
                "usage": {"total_tokens": 42},
            },
        )

    b.settings.background_generation_enabled = background_mode
    b.settings.primary_question_assessment = True
    b.settings.question_prompt_version = version
    gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.source_settings,
        presales_settings=b.settings,
        transport=httpx.MockTransport(respond),
    )
    b.service.generation.gateway = gateway
    payload = b.payload.model_copy(
        update={"requirements": [RequirementInput(key="Q", text=QUESTION)]}
    )
    packet = await b.service.create(b.context.principal, payload, "context-create")
    await b.service.generate(
        b.context.principal, packet.id, packet.rows[0].id, "context-once", execution_mode="auto"
    )
    if background_mode:
        assert await BackgroundGeneration(b.service.generation, {"primary": gateway}).run_once(
            "window"
        )
    row = (await b.service.get(b.context.principal, packet.id)).rows[0]
    assert row.state == "drafted", row.attempts
    assert len(calls) == 1 and row.attempts[0].provider_request_count == 1
    assert row.draft.retrieval[0].used_count == (3 if version == "presales.v26" else 2)
    if version == "presales.v26":
        citation = row.draft.citations[0]
        assert citation.chunk_id == chunks[1].chunk_id
        assert citation.document_version_id == source.version_id
        assert citation.excerpt in chunks[1].text
        assert (citation.start_offset, citation.end_offset) == (1200, 2400)
    await b.service.generate(
        b.context.principal, packet.id, packet.rows[0].id, "context-once", execution_mode="auto"
    )
    assert len(calls) == 1
    async with b.sessions() as session:
        reservations = (await session.scalars(select(UsageReservation))).all()
        assert len(reservations) == 1 and reservations[0].state == "consumed"


@pytest.mark.parametrize("background_mode", [False, True])
async def test_revocation_after_context_lookup_prevents_provider_dispatch(
    background, monkeypatch, background_mode
):
    b = background
    await seed_chunks(b)
    from enterprise_doc_core.presales import generation as module

    original = module.expand_context

    async def revoked_after_lookup(*args, **kwargs):
        result = await original(*args, **kwargs)
        async with b.sessions.begin() as session:
            (await session.get(DocumentVersion, b.context.document_version_id)).status = "failed"
        return result

    monkeypatch.setattr(module, "expand_context", revoked_after_lookup)
    b.settings.background_generation_enabled = background_mode
    b.settings.primary_question_assessment = True
    b.settings.question_prompt_version = "presales.v26"
    b.service.generation.gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.source_settings,
        presales_settings=b.settings,
        transport=httpx.MockTransport(lambda request: pytest.fail("revoked source was sent")),
    )
    packet = await b.service.create(
        b.context.principal,
        b.payload.model_copy(update={"requirements": [RequirementInput(key="Q", text=QUESTION)]}),
        "create",
    )
    if background_mode:
        await b.service.generate(
            b.context.principal, packet.id, packet.rows[0].id, "generate", execution_mode="auto"
        )
    else:
        # The attempt fails before dispatch; the service's final GET also rejects
        # the now-revoked source instead of returning its old packet snapshot.
        with pytest.raises(PresalesError, match="presales_source_unavailable"):
            await b.service.generate(
                b.context.principal, packet.id, packet.rows[0].id, "generate", execution_mode="auto"
            )
    if background_mode:
        worker = BackgroundGeneration(
            b.service.generation, {"primary": b.service.generation.gateway}
        )
        await worker.run_once("revocation")
    async with b.sessions.begin() as session:
        (await session.get(DocumentVersion, b.context.document_version_id)).status = "ready"
    row = (await b.service.get(b.context.principal, packet.id)).rows[0]
    assert row.state == "failed" and row.draft is None
    assert row.attempts[0].provider_request_count == 0
