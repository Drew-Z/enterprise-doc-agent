import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select

from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.documents.models import DocumentChunk, DocumentIngestionGeneration
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import RequirementInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.jobs.test_m3_retrieval_integration import (
    VECTOR_A,
    VECTOR_B,
    ControlledEmbeddingProvider,
)
from tests.presales.test_presales_background_integration import background as background

pytestmark = pytest.mark.integration
CASES = json.loads(Path(__file__).with_name("question_context_regressions.json").read_bytes())[
    "cases"
]


async def seed_source(b, case):
    text = case["source_text"]
    assert hashlib.sha256(text.encode()).hexdigest() == case["source_sha256"]
    async with b.sessions.begin() as session:
        await session.execute(
            delete(DocumentChunk).where(DocumentChunk.generation_id == b.context.generation_id)
        )
        chunks = []
        for index, offset in enumerate(range(0, len(text), 1080)):
            excerpt = text[offset : offset + 1200]
            # Controlled vectors favor the two historically cited locations.
            # Keyword SQL, RRF, selection, authorization and projection remain real.
            chunks.append(
                DocumentChunk(
                    id=uuid4(),
                    tenant_id=b.context.tenant_id,
                    document_version_id=b.context.document_version_id,
                    generation_id=b.context.generation_id,
                    chunk_index=index,
                    start_offset=offset,
                    end_offset=offset + len(excerpt),
                    normalized_text=excerpt,
                    content_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                    search_vector=func.to_tsvector("simple", excerpt),
                    embedding=list(
                        VECTOR_A if offset in case["historical_cited_offsets"] else VECTOR_B
                    ),
                )
            )
        session.add_all(chunks)
        generation = await session.get(DocumentIngestionGeneration, b.context.generation_id)
        generation.chunk_count = generation.embedded_count = len(chunks)
    embeddings = ControlledEmbeddingProvider({case["question"]: VECTOR_A})
    b.service.generation.retriever = HybridRetrievalService(
        session_factory=b.sessions, embedding_provider=embeddings
    )
    return embeddings


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["original_id"])
@pytest.mark.parametrize("background_mode", [False, True])
@pytest.mark.parametrize("version", ["presales.v24", "presales.v25"])
async def test_known_public_scope_and_password_reach_real_generation_with_same_budget(
    background, case, background_mode, version
):
    b = background
    await seed_source(b, case)
    b.settings.background_generation_enabled = background_mode
    b.settings.primary_question_assessment = True
    b.settings.question_prompt_version = version
    request_bodies = []

    def respond(request):
        body = json.loads(request.content)
        wire = json.loads(body["messages"][1]["content"])
        request_bodies.append(body)
        assert (len(wire["requirementParts"]) == 1) is (version == "presales.v25")
        assert "".join(part["text"] for part in wire["requirementParts"]) == case["question"]
        matches = [item for item in wire["evidence"] if case["required_excerpt"] in item["text"]]
        assert bool(matches) is (version == "presales.v25")
        match = matches[0] if matches else wire["evidence"][0]
        result = {
            "rules": [],
            "assessments": [],
            "responses": [
                {
                    "requirementPartId": part["requirementPartId"],
                    "answer": "资料明确说明范围或数值。测试只验证原文可达。",
                    "citations": [{"citationId": match["citationId"]}],
                    "missingInformation": ["其他询问所需的实际实施情况仍需核实。"],
                }
                for part in wire["requirementParts"]
            ],
            "status": "insufficient_evidence",
            "conclusion": "仍需核实未获证明的事项。",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"total_tokens": 42},
            },
        )

    template = b.service.generation.gateway
    gateway = OpenAICompatiblePresalesGateway(
        template.source_settings,
        presales_settings=b.settings,
        transport=httpx.MockTransport(respond),
    )
    b.service.generation.gateway = gateway
    payload = b.payload.model_copy(
        update={"requirements": [RequirementInput(key=case["original_id"], text=case["question"])]}
    )
    packet = await b.service.create(b.context.principal, payload, "context-create")
    await b.service.generate(b.context.principal, packet.id, packet.rows[0].id, "context-once")
    if background_mode:
        worker = BackgroundGeneration(b.service.generation, {"primary": gateway})
        assert await worker.run_once("context-worker")
    refreshed = await b.service.get(b.context.principal, packet.id)
    row = refreshed.rows[0]
    assert len(request_bodies) == 1
    sent = json.loads(request_bodies[0]["messages"][1]["content"])
    assert any(case["required_excerpt"] in item["text"] for item in sent["evidence"]) is (
        version == "presales.v25"
    )
    assert row.state == "drafted", row.attempts
    assert len(request_bodies) == 1 and row.attempts[0].provider_request_count == 1
    assert row.attempts[0].provenance["promptVersion"] == version
    assert (case["required_excerpt"] in row.draft.citations[0].excerpt) is (
        version == "presales.v25"
    )
    assert row.draft.citations[0].excerpt in case["source_text"]
    assert row.draft.retrieval[0].used_count == 2
    assert row.draft.retrieval[0].retrieved_count <= 10
    assert row.draft.retrieval[0].truncated
    await b.service.generate(b.context.principal, packet.id, row.id, "context-once")
    assert len(request_bodies) == 1
    async with b.sessions() as session:
        reservations = (await session.scalars(select(UsageReservation))).all()
        assert len(reservations) == 1 and reservations[0].state == "consumed"
