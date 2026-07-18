from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.documents.ingestion import EmbeddingProvider
from enterprise_doc_core.documents.models import (
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
)
from enterprise_doc_core.documents.retrieval import (
    RetrievalCandidate,
    RetrievalDecision,
    decide_retrieval,
    reciprocal_rank_fusion,
)


class HybridRetrievalService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        top_k: int = 10,
        rrf_k: int = 60,
        min_score: float | None = None,
        min_candidates: int = 1,
        max_vector_distance: float = 0.65,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        resolved_min_score = 1.0 / (rrf_k + 1) if min_score is None else min_score
        if resolved_min_score < 0:
            raise ValueError("min_score must be non-negative")
        if min_candidates <= 0 or min_candidates > top_k:
            raise ValueError("min_candidates must be in [1, top_k]")
        if not 0 <= max_vector_distance <= 2:
            raise ValueError("max_vector_distance must be in [0, 2]")
        self.session_factory = session_factory
        self.embedding_provider = embedding_provider
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.min_score = resolved_min_score
        self.min_candidates = min_candidates
        self.max_vector_distance = max_vector_distance

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision:
        normalized_query = query.strip()
        if not normalized_query:
            return decide_retrieval(
                (),
                min_score=self.min_score,
                min_candidates=self.min_candidates,
            )
        keyword_candidates = await self._keyword_recall(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            query=normalized_query,
        )
        vectors = await self.embedding_provider.embed((normalized_query,))
        if len(vectors) != 1:
            raise ValueError("embedding provider returned an invalid query batch")
        vector_candidates = await self._vector_recall(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            vector=vectors[0],
        )
        fused = reciprocal_rank_fusion(
            keyword_candidates,
            vector_candidates,
            rrf_k=self.rrf_k,
            top_k=self.top_k,
        )
        return decide_retrieval(
            fused,
            min_score=self.min_score,
            min_candidates=self.min_candidates,
        )

    async def _keyword_recall(
        self, *, tenant_id: UUID, document_version_id: UUID, query: str
    ) -> tuple[RetrievalCandidate, ...]:
        ts_query = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, ts_query)
        statement = (
            select(
                DocumentChunk.id,
                DocumentChunk.tenant_id,
                DocumentChunk.document_version_id,
                DocumentChunk.generation_id,
                DocumentChunk.normalized_text,
                DocumentChunk.page_number,
                DocumentChunk.heading,
                DocumentChunk.start_offset,
                DocumentChunk.end_offset,
                DocumentVersion.original_filename,
                rank.label("rank_score"),
            )
            .join(
                DocumentIngestionGeneration,
                DocumentIngestionGeneration.id == DocumentChunk.generation_id,
            )
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_version_id == document_version_id,
                DocumentIngestionGeneration.tenant_id == DocumentChunk.tenant_id,
                DocumentIngestionGeneration.document_version_id
                == DocumentChunk.document_version_id,
                DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                DocumentIngestionGeneration.active.is_(True),
                DocumentVersion.tenant_id == DocumentChunk.tenant_id,
                DocumentChunk.search_vector.op("@@")(ts_query),
            )
            .order_by(desc(rank), DocumentChunk.chunk_index)
            .limit(self.top_k)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(self._candidate_from_row(row, score=float(row.rank_score)) for row in rows)

    async def _vector_recall(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        vector: Sequence[float],
    ) -> tuple[RetrievalCandidate, ...]:
        distance = DocumentChunk.embedding.cosine_distance(list(vector))
        statement = (
            select(
                DocumentChunk.id,
                DocumentChunk.tenant_id,
                DocumentChunk.document_version_id,
                DocumentChunk.generation_id,
                DocumentChunk.normalized_text,
                DocumentChunk.page_number,
                DocumentChunk.heading,
                DocumentChunk.start_offset,
                DocumentChunk.end_offset,
                DocumentVersion.original_filename,
                distance.label("distance"),
            )
            .join(
                DocumentIngestionGeneration,
                DocumentIngestionGeneration.id == DocumentChunk.generation_id,
            )
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_version_id == document_version_id,
                DocumentChunk.embedding.is_not(None),
                DocumentIngestionGeneration.tenant_id == DocumentChunk.tenant_id,
                DocumentIngestionGeneration.document_version_id
                == DocumentChunk.document_version_id,
                DocumentIngestionGeneration.status == DocumentIngestionStatus.SUCCEEDED.value,
                DocumentIngestionGeneration.stage == DocumentIngestionStage.READY.value,
                DocumentIngestionGeneration.active.is_(True),
                DocumentVersion.tenant_id == DocumentChunk.tenant_id,
                distance <= self.max_vector_distance,
            )
            .order_by(distance, DocumentChunk.chunk_index)
            .limit(self.top_k)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(self._candidate_from_row(row, score=1.0 - float(row.distance)) for row in rows)

    @staticmethod
    def _candidate_from_row(row: Any, *, score: float) -> RetrievalCandidate:
        return RetrievalCandidate(
            chunk_id=row.id,
            tenant_id=row.tenant_id,
            document_version_id=row.document_version_id,
            generation_id=row.generation_id,
            text=row.normalized_text,
            page_number=row.page_number,
            heading=row.heading,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            source_filename=row.original_filename,
            score=score,
        )
