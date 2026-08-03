from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.documents.ingestion import EmbeddingProvider
from enterprise_doc_core.documents.models import (
    DEFAULT_EMBEDDING_DIMENSION,
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
from enterprise_doc_core.telemetry import MetricsRuntime


def format_embedding_query(query: str, instruction: str | None) -> str:
    normalized_instruction = instruction.strip() if instruction else ""
    if not normalized_instruction:
        return query
    return f"Instruct: {normalized_instruction}\nQuery:{query}"


class HybridRetrievalService:
    _QUERY_STOPWORDS = frozenset(
        {
            "a",
            "about",
            "according",
            "an",
            "and",
            "are",
            "can",
            "could",
            "do",
            "does",
            "for",
            "from",
            "how",
            "in",
            "is",
            "me",
            "of",
            "on",
            "please",
            "tell",
            "the",
            "to",
            "what",
            "which",
            "where",
            "who",
            "why",
        }
    )

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
        embedding_model: str | None = None,
        embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        query_instruction: str | None = None,
        top_k: int = 10,
        rrf_k: int = 60,
        min_score: float | None = None,
        min_candidates: int = 1,
        max_vector_distance: float = 0.65,
        metrics: MetricsRuntime | None = None,
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
        if embedding_dimension != DEFAULT_EMBEDDING_DIMENSION:
            raise ValueError(
                "current storage contract requires "
                f"{DEFAULT_EMBEDDING_DIMENSION}-dimensional embeddings"
            )
        self.session_factory = session_factory
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.query_instruction = query_instruction.strip() if query_instruction else None
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.min_score = resolved_min_score
        self.min_candidates = min_candidates
        self.max_vector_distance = max_vector_distance
        self.metrics = metrics

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        query: str,
    ) -> RetrievalDecision:
        started = perf_counter()
        try:
            decision = await self._retrieve(
                tenant_id=tenant_id,
                document_version_id=document_version_id,
                query=query,
            )
        except asyncio.CancelledError:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="retrieval",
                    operation="retrieve",
                    result="cancelled",
                    duration=perf_counter() - started,
                )
            raise
        except Exception:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="retrieval",
                    operation="retrieve",
                    result="error",
                    duration=perf_counter() - started,
                )
            raise
        if self.metrics is not None:
            self.metrics.observe_boundary(
                boundary="retrieval",
                operation="retrieve",
                result="success" if decision.accepted else "refused",
                duration=perf_counter() - started,
            )
        return decision

    async def _retrieve(
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
        embedding_query = format_embedding_query(normalized_query, self.query_instruction)
        vectors = await self.embedding_provider.embed((embedding_query,))
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
        candidates = await self._execute_keyword_recall(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            ts_query=ts_query,
            rank=rank,
        )
        if candidates:
            return candidates

        # Natural-language questions often contain words absent from the
        # document. Retry with meaningful terms so exact lexical overlap can
        # still complement semantic/vector recall.
        fallback_query = self._keyword_fallback_query(query)
        if fallback_query is None:
            return ()
        fallback_ts_query = func.websearch_to_tsquery("simple", fallback_query)
        fallback_rank = func.ts_rank_cd(DocumentChunk.search_vector, fallback_ts_query)
        return await self._execute_keyword_recall(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            ts_query=fallback_ts_query,
            rank=fallback_rank,
        )

    async def _execute_keyword_recall(
        self,
        *,
        tenant_id: UUID,
        document_version_id: UUID,
        ts_query: Any,
        rank: Any,
    ) -> tuple[RetrievalCandidate, ...]:
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
                DocumentIngestionGeneration.embedding_dimension == self.embedding_dimension,
                DocumentVersion.tenant_id == DocumentChunk.tenant_id,
                DocumentChunk.search_vector.op("@@")(ts_query),
            )
            .order_by(desc(rank), DocumentChunk.chunk_index)
            .limit(self.top_k)
        )
        if self.embedding_model is not None:
            statement = statement.where(
                DocumentIngestionGeneration.embedding_model == self.embedding_model
            )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(self._candidate_from_row(row, score=float(row.rank_score)) for row in rows)

    @classmethod
    def _keyword_fallback_query(cls, query: str) -> str | None:
        terms = re.findall(r"[^\W_]+", query.casefold(), flags=re.UNICODE)
        meaningful_terms = tuple(
            term for term in terms if len(term) > 1 and term not in cls._QUERY_STOPWORDS
        )
        if not meaningful_terms:
            return None
        return " OR ".join(meaningful_terms)

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
                DocumentIngestionGeneration.embedding_dimension == self.embedding_dimension,
                DocumentVersion.tenant_id == DocumentChunk.tenant_id,
                distance <= self.max_vector_distance,
            )
            .order_by(distance, DocumentChunk.chunk_index)
            .limit(self.top_k)
        )
        if self.embedding_model is not None:
            statement = statement.where(
                DocumentIngestionGeneration.embedding_model == self.embedding_model
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
