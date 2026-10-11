"""Bounded Haystack windows over existing authorized chunks, without merged citations."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.db import read_only_session
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.policy import document_visible_to_actor
from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.presales.access import source_snapshots
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import SourceInput, SourceSnapshot

if TYPE_CHECKING:
    from haystack import Document as HaystackDocument
    from haystack.document_stores.types import DocumentStore

WINDOW_RADIUS = 3
SOURCE_CHAR_BUDGET = 3600
CHUNK_CHAR_BUDGET = 1800
TOTAL_CHUNK_BUDGET = 12
MAX_WINDOW_CHUNKS = 2 * (2 * WINDOW_RADIUS + 1)
_SCOPE_FIELDS = ["tenant_id", "document_version_id", "generation_id"]


@dataclass(frozen=True)
class WindowChunk:
    candidate: RetrievalCandidate
    index: int


@dataclass(frozen=True)
class WindowEvidence:
    candidates: tuple[RetrievalCandidate, ...]
    available_ids: frozenset[UUID]
    truncated: bool


@dataclass
class _BoundedStore:
    """Per-call read-only store; no global index, executor, persistence or external I/O."""

    documents: list[HaystackDocument]

    def filter_documents(self, filters: dict[str, Any] | None = None) -> list[HaystackDocument]:
        from haystack.utils.filters import document_matches_filter

        return [d for d in self.documents if filters is None or document_matches_filter(filters, d)]


def select_context(
    seeds: tuple[RetrievalCandidate, ...],
    chunks: tuple[WindowChunk, ...],
    *,
    extra_slots: int,
) -> WindowEvidence:
    if not 0 <= extra_slots <= TOTAL_CHUNK_BUDGET - len(seeds) or len(seeds) > 2:
        raise PresalesError("presales_invalid_evidence")
    available = context_candidates(seeds, chunks)
    remaining = SOURCE_CHAR_BUDGET - sum(len(seed.text) for seed in seeds)
    selected = list(seeds)
    for item in available[len(seeds) :]:
        if extra_slots > 0 and len(item.text) <= remaining:
            selected.append(item)
            remaining -= len(item.text)
            extra_slots -= 1
    return WindowEvidence(
        tuple(selected), frozenset(c.chunk_id for c in available), len(selected) < len(available)
    )


def context_candidates(
    seeds: tuple[RetrievalCandidate, ...], chunks: tuple[WindowChunk, ...]
) -> tuple[RetrievalCandidate, ...]:
    """Discover and validate an unallocated window, seeds first, then proximity order."""
    if len(seeds) > 2:
        raise PresalesError("presales_invalid_evidence")
    if not seeds:
        return ()
    if len(chunks) > MAX_WINDOW_CHUNKS:
        raise PresalesError("presales_invalid_evidence")
    scope = tuple(getattr(seeds[0], key) for key in _SCOPE_FIELDS)
    by_id = {chunk.candidate.chunk_id: chunk for chunk in chunks}
    if len(by_id) != len(chunks) or len({c.index for c in chunks}) != len(chunks):
        raise PresalesError("presales_invalid_evidence")
    if len({c.chunk_id for c in seeds}) != len(seeds):
        raise PresalesError("presales_invalid_evidence")
    for item in [*seeds, *(c.candidate for c in chunks)]:
        if (
            tuple(getattr(item, key) for key in _SCOPE_FIELDS) != scope
            or not 0 < len(item.text) <= CHUNK_CHAR_BUDGET
            or item.end_offset != item.start_offset + len(item.text)
            or item.start_offset < 0
        ):
            raise PresalesError("presales_invalid_evidence")
    for seed in seeds:
        original = by_id.get(seed.chunk_id)
        if original is None or any(
            getattr(seed, field) != getattr(original.candidate, field)
            for field in (
                "text",
                "start_offset",
                "end_offset",
                "page_number",
                "heading",
                "source_filename",
            )
        ):
            raise PresalesError("presales_invalid_evidence")
    if any(chunk.index < 0 for chunk in chunks):
        raise PresalesError("presales_invalid_evidence")

    # Library opt-out must precede the first import: telemetry otherwise creates a
    # user-level config/client. This application does not use Haystack tracing.
    os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"
    os.environ["HAYSTACK_AUTO_TRACE_ENABLED"] = "false"
    from haystack import Document as HaystackDocument
    from haystack.components.retrievers import SentenceWindowRetriever

    documents = [
        HaystackDocument(
            id=str(c.candidate.chunk_id),
            content=c.candidate.text,
            meta={
                **{key: str(getattr(c.candidate, key)) for key in _SCOPE_FIELDS},
                "split_id": c.index,
                "split_idx_start": c.candidate.start_offset,
            },
        )
        for c in chunks
    ]
    documents_by_id = {d.id: d for d in documents}
    retriever = SentenceWindowRetriever(
        cast("DocumentStore", _BoundedStore(documents)),
        window_size=WINDOW_RADIUS,
        source_id_meta_field=_SCOPE_FIELDS,
    )
    result = retriever.run([documents_by_id[str(c.chunk_id)] for c in seeds])
    # Only the original documents are useful for citations. Never project merged
    # context_windows onto an existing chunk ID or use them as new source records.
    window_ids = {UUID(d.id) for d in result["context_documents"]}
    if not {c.chunk_id for c in seeds} <= window_ids or not window_ids <= by_id.keys():
        raise PresalesError("presales_invalid_evidence")
    seed_ids = {seed.chunk_id for seed in seeds}
    neighbors = sorted(
        (by_id[key] for key in window_ids - seed_ids),
        key=lambda c: (
            min(abs(c.index - by_id[s.chunk_id].index) for s in seeds),
            min((abs(c.index - by_id[s.chunk_id].index), i) for i, s in enumerate(seeds))[1],
            c.index,
        ),
    )
    return (*seeds, *(neighbor.candidate for neighbor in neighbors))


ContextWindow = tuple[tuple[RetrievalCandidate, ...], tuple[WindowChunk, ...]]


def select_ranked_context(
    windows: tuple[ContextWindow, ...], scores: Mapping[UUID, float]
) -> tuple[WindowEvidence, ...]:
    """Candidate-only allocator; scores never supply text, authorization or semantics."""
    if len(windows) > 6:
        raise PresalesError("presales_invalid_evidence")
    pools = [context_candidates(seeds, chunks) for seeds, chunks in windows]
    all_candidates = [c for pool in pools for c in pool]
    ids = {c.chunk_id for c in all_candidates}
    versions = [pool[0].document_version_id for pool in pools if pool]
    if (
        len(ids) != len(all_candidates)
        or len(set(versions)) != len(versions)
        or len({c.tenant_id for c in all_candidates}) > 1
        or set(scores) != ids
        or any(
            type(score) not in {float, int} or not math.isfinite(score) for score in scores.values()
        )
    ):
        raise PresalesError("presales_invalid_evidence")
    selected = [list(seeds) for seeds, _ in windows]
    remaining = [SOURCE_CHAR_BUDGET - sum(len(c.text) for c in seeds) for seeds, _ in windows]
    slots = TOTAL_CHUNK_BUDGET - sum(len(seeds) for seeds, _ in windows)
    neighbors = sorted(
        (
            (source_index, proximity, candidate)
            for source_index, ((seeds, _), pool) in enumerate(zip(windows, pools, strict=True))
            for proximity, candidate in enumerate(pool[len(seeds) :])
        ),
        key=lambda item: (
            -scores[item[2].chunk_id],
            str(item[2].document_version_id),
            item[1],
        ),
    )
    for source_index, _, candidate in neighbors:
        if slots > 0 and len(candidate.text) <= remaining[source_index]:
            selected[source_index].append(candidate)
            remaining[source_index] -= len(candidate.text)
            slots -= 1
    return tuple(
        WindowEvidence(tuple(chosen), frozenset(c.chunk_id for c in pool), len(chosen) < len(pool))
        for chosen, pool in zip(selected, pools, strict=True)
    )


async def expand_context(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: UUID,
    actor_id: UUID,
    source: SourceSnapshot,
    seeds: tuple[RetrievalCandidate, ...],
    extra_slots: int,
) -> WindowEvidence:
    if not seeds:
        return WindowEvidence((), frozenset(), False)
    if len(seeds) > 2 or any(
        (c.tenant_id, c.document_version_id, c.generation_id)
        != (tenant_id, source.version_id, source.generation_id)
        for c in seeds
    ):
        raise PresalesError("presales_invalid_evidence")
    # This CTE only locates indexes; both it and the materialized rows bind the
    # immutable generation. The outer query additionally rechecks active ACLs.
    seed_indexes = (
        select(DocumentChunk.chunk_index.label("seed_index"))
        .where(
            DocumentChunk.id.in_([seed.chunk_id for seed in seeds]),
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_version_id == source.version_id,
            DocumentChunk.generation_id == source.generation_id,
        )
        .cte("context_seeds")
    )
    statement = (
        select(
            DocumentChunk.id,
            DocumentChunk.chunk_index,
            func.left(DocumentChunk.normalized_text, CHUNK_CHAR_BUDGET).label("text"),
            func.length(DocumentChunk.normalized_text).label("full_length"),
            DocumentChunk.start_offset,
            DocumentChunk.end_offset,
            DocumentChunk.page_number,
            DocumentChunk.heading,
            DocumentVersion.original_filename,
        )
        .join(
            DocumentIngestionGeneration,
            DocumentIngestionGeneration.id == DocumentChunk.generation_id,
        )
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_version_id == source.version_id,
            DocumentChunk.generation_id == source.generation_id,
            DocumentIngestionGeneration.tenant_id == tenant_id,
            DocumentIngestionGeneration.document_version_id == source.version_id,
            DocumentIngestionGeneration.active.is_(True),
            DocumentIngestionGeneration.status == "succeeded",
            DocumentIngestionGeneration.stage == "ready",
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.status == "ready",
            document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
            select(seed_indexes.c.seed_index)
            .where(func.abs(DocumentChunk.chunk_index - seed_indexes.c.seed_index) <= WINDOW_RADIUS)
            .exists(),
        )
        .order_by(DocumentChunk.chunk_index)
        .limit(MAX_WINDOW_CHUNKS + 1)
    )
    async with read_only_session(session_factory) as session:
        actual = await source_snapshots(
            session,
            tenant_id,
            actor_id,
            [SourceInput(version_id=source.version_id, applicability=source.applicability)],
        )
        if actual != [source]:
            raise PresalesError("presales_stale_sources")
        rows = (await session.execute(statement)).all()
    chunks = []
    seed_ids = {seed.chunk_id for seed in seeds}
    for row in rows:
        if row.end_offset != row.start_offset + row.full_length:
            raise PresalesError("presales_invalid_evidence")
        if row.id not in seed_ids and row.full_length > CHUNK_CHAR_BUDGET:
            # Preserve historical seed prefixes, but never introduce a new
            # neighbor by silently cutting an oversized clause to make it fit.
            continue
        chunks.append(
            WindowChunk(
                RetrievalCandidate(
                    chunk_id=row.id,
                    tenant_id=tenant_id,
                    document_version_id=source.version_id,
                    generation_id=source.generation_id,
                    text=row.text,
                    start_offset=row.start_offset,
                    end_offset=row.start_offset + len(row.text),
                    page_number=row.page_number,
                    heading=row.heading,
                    source_filename=row.original_filename,
                ),
                row.chunk_index,
            )
        )
    selected = select_context(seeds, tuple(chunks), extra_slots=extra_slots)
    return WindowEvidence(
        selected.candidates,
        frozenset(row.id for row in rows),
        selected.truncated or any(row.full_length > CHUNK_CHAR_BUDGET for row in rows),
    )
