from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import uuid4

from enterprise_doc_core.documents import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
    HashEmbeddingProvider,
)
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import GeneratedDraft, GenerationInput, ModelDraft
from enterprise_doc_core.uploads import UploadSession
from tests.agent.test_agent_run_integration import SeededAgentContext


async def add_document(session_factory: Any, context: SeededAgentContext) -> tuple:
    document_id, version_id, upload_id, generation_id = (uuid4() for _ in range(4))
    async with session_factory.begin() as session:
        base_version = await session.get(DocumentVersion, context.document_version_id)
        base_upload = await session.get(UploadSession, base_version.upload_session_id)
        base_generation = await session.get(DocumentIngestionGeneration, context.generation_id)
        upload_values = {
            c.key: getattr(base_upload, c.key)
            for c in UploadSession.__table__.columns
            if c.key not in {"id", "created_at", "updated_at", "document_version_id"}
        }
        upload_values.update(
            pending_document_id=document_id,
            pending_version_id=version_id,
            object_key=f"presales-test/{version_id}/policy.txt",
            idempotency_key=f"presales-test-{uuid4()}",
        )
        upload = UploadSession(id=upload_id, **upload_values)
        session.add(upload)
        session.add(
            Document(
                id=document_id,
                tenant_id=context.tenant_id,
                created_by=context.actor_id,
                title="Backup policy",
            )
        )
        await session.flush()
        version_values = {
            c.key: getattr(base_version, c.key)
            for c in DocumentVersion.__table__.columns
            if c.key not in {"id", "created_at", "updated_at"}
        }
        version_values.update(
            document_id=document_id,
            upload_session_id=upload_id,
            object_key=upload.object_key,
            original_filename="backup-policy.txt",
        )
        session.add(DocumentVersion(id=version_id, **version_values))
        await session.flush()
        upload.document_version_id = version_id
        generation_values = {
            c.key: getattr(base_generation, c.key)
            for c in DocumentIngestionGeneration.__table__.columns
            if c.key not in {"id", "created_at", "updated_at"}
        }
        generation_values.update(document_version_id=version_id)
        session.add(DocumentIngestionGeneration(id=generation_id, **generation_values))
    return version_id, generation_id


async def add_chunk(
    session_factory: Any, context: SeededAgentContext, version_id, generation_id, text: str
) -> None:
    vector = list((await HashEmbeddingProvider().embed((text,)))[0])
    async with session_factory.begin() as session:
        session.add(
            DocumentChunk(
                id=uuid4(),
                tenant_id=context.tenant_id,
                document_version_id=version_id,
                generation_id=generation_id,
                chunk_index=0,
                page_number=1,
                heading="Retention",
                start_offset=0,
                end_offset=len(text),
                normalized_text=text,
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                search_vector="'retention':1",
                embedding=vector,
            )
        )
        generation = await session.get(DocumentIngestionGeneration, generation_id)
        generation.chunk_count = generation.embedded_count = 1


class ControlledGateway:
    model_provider = "test_http_boundary"
    model_name = "controlled-presales"

    def __init__(self) -> None:
        self.provenance: dict[str, str | None] = {"promptVersion": "controlled-test.v1"}
        self.calls: list[GenerationInput] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail_next = False

    async def generate(self, payload: GenerationInput) -> GeneratedDraft:
        self.calls.append(payload)
        self.entered.set()
        await self.release.wait()
        if self.fail_next:
            self.fail_next = False
            raise PresalesError("presales_model_timeout", provider_requests=1)
        citations = [
            {
                "chunkId": e["chunkId"],
                "documentVersionId": e["documentVersionId"],
                "excerpt": e["text"][:600],
            }
            for e in payload.evidence
        ]
        if not citations:
            return GeneratedDraft(
                draft=ModelDraft(
                    status="insufficient_evidence",
                    answer="没有相关片段。",
                    missing_information=["请补充保留期限证明。"],
                    citations=[],
                )
            )
        status = (
            "conflicting_evidence"
            if len({c["documentVersionId"] for c in citations}) > 1
            else "supported"
        )
        return GeneratedDraft(
            draft=ModelDraft(
                status=status,
                answer="资料约定的保留时间存在差异。"
                if status == "conflicting_evidence"
                else "依据资料填写。",
                citations=citations,
            )
        )
