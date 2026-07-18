from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.documents.evaluation import (
    RetrievalEvalCase,
    evaluate_retrieval_cases,
)
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus


@dataclass(frozen=True, slots=True)
class CorpusItem:
    key: str
    text: str
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LiveCase:
    case_id: str
    query: str
    query_embedding: tuple[float, ...]
    relevant_chunk_keys: tuple[str, ...]
    expected_refusal: bool


@dataclass(frozen=True, slots=True)
class LiveDataset:
    version: str
    limitations: str
    k: int
    top_k: int
    rrf_k: int
    min_candidates: int
    max_vector_distance: float
    corpus: tuple[CorpusItem, ...]
    distractors: tuple[CorpusItem, ...]
    cases: tuple[LiveCase, ...]


class DatasetEmbeddingProvider:
    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        try:
            return tuple(self.vectors[text] for text in texts)
        except KeyError as error:
            raise ValueError("evaluation query is missing a deterministic embedding") from error


def _vector(raw: Any) -> tuple[float, ...]:
    vector = tuple(float(value) for value in raw)
    if len(vector) != 8:
        raise ValueError("evaluation embeddings must have dimension 8")
    return vector


def _item(raw: dict[str, Any]) -> CorpusItem:
    return CorpusItem(
        key=str(raw["key"]),
        text=str(raw["text"]),
        embedding=_vector(raw["embedding"]),
    )


def _live_case(raw: dict[str, Any]) -> LiveCase:
    return LiveCase(
        case_id=str(raw["case_id"]),
        query=str(raw["query"]),
        query_embedding=_vector(raw["query_embedding"]),
        relevant_chunk_keys=tuple(map(str, raw["relevant_chunk_keys"])),
        expected_refusal=bool(raw["expected_refusal"]),
    )


def load_live_dataset(path: Path) -> LiveDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    dataset = LiveDataset(
        version=str(payload["version"]),
        limitations=str(payload["limitations"]),
        k=int(payload["k"]),
        top_k=int(payload["top_k"]),
        rrf_k=int(payload["rrf_k"]),
        min_candidates=int(payload["min_candidates"]),
        max_vector_distance=float(payload["max_vector_distance"]),
        corpus=tuple(_item(raw) for raw in payload["corpus"]),
        distractors=tuple(_item(raw) for raw in payload.get("distractors", ())),
        cases=tuple(_live_case(raw) for raw in payload["cases"]),
    )
    corpus_keys = {item.key for item in dataset.corpus}
    if len(corpus_keys) != len(dataset.corpus):
        raise ValueError("evaluation corpus keys must be unique")
    if any(
        relevant_key not in corpus_keys
        for case in dataset.cases
        for relevant_key in case.relevant_chunk_keys
    ):
        raise ValueError("evaluation case references an unknown corpus key")
    return dataset


def _stable_id(dataset_version: str, kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"enterprise-doc-agent:{dataset_version}:{kind}:{key}")


async def _seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: LiveDataset,
) -> tuple[UUID, UUID]:
    tenant_id = _stable_id(dataset.version, "tenant", "live-eval")
    actor_id = _stable_id(dataset.version, "actor", "live-eval")
    async with session_factory.begin() as session:
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.execute(delete(User).where(User.id == actor_id))
        session.add(
            Tenant(
                id=tenant_id,
                name=f"M3 live evaluation {dataset.version}",
                slug=f"m3-live-eval-{hashlib.sha256(dataset.version.encode()).hexdigest()[:12]}",
                quota_bytes=16 * 1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"{actor_id}@evaluation.invalid"))
    return tenant_id, actor_id


async def _seed_corpus_version(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: LiveDataset,
    tenant_id: UUID,
    actor_id: UUID,
    corpus_name: str,
    items: tuple[CorpusItem, ...],
) -> tuple[UUID, dict[str, UUID]]:
    document_id = _stable_id(dataset.version, "document", corpus_name)
    version_id = _stable_id(dataset.version, "version", corpus_name)
    upload_id = _stable_id(dataset.version, "upload", corpus_name)
    generation_id = _stable_id(dataset.version, "generation", corpus_name)
    filename = f"{corpus_name}.txt"
    body = "\n".join(item.text for item in items).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    now = datetime.now(UTC)
    chunk_ids = {
        item.key: _stable_id(dataset.version, f"chunk:{corpus_name}", item.key) for item in items
    }
    async with session_factory.begin() as session:
        session.add(
            UploadSession(
                id=upload_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                pending_document_id=document_id,
                pending_version_id=version_id,
                status=UploadSessionStatus.COMPLETED.value,
                idempotency_key=f"live-eval:{dataset.version}:{corpus_name}",
                request_fingerprint=digest,
                object_key=f"{tenant_id}/evaluation/{version_id}/{filename}",
                original_filename=filename,
                extension=".txt",
                declared_media_type="text/plain",
                size_bytes=len(body),
                declared_sha256=digest,
                part_size_bytes=len(body),
                expected_part_count=1,
                reserved_bytes=0,
                expires_at=now + timedelta(hours=1),
                completed_at=now,
            )
        )
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                created_by=actor_id,
                title=f"M3 live evaluation {corpus_name}",
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                tenant_id=tenant_id,
                document_id=document_id,
                upload_session_id=upload_id,
                version_number=1,
                status=DocumentVersionStatus.READY.value,
                object_key=f"{tenant_id}/evaluation/{version_id}/{filename}",
                original_filename=filename,
                declared_media_type="text/plain",
                detected_media_type="text/plain",
                size_bytes=len(body),
                declared_sha256=digest,
                created_by=actor_id,
            )
        )
        await session.flush()
        upload = await session.get(UploadSession, upload_id)
        assert upload is not None
        upload.document_version_id = version_id
        session.add(
            DocumentIngestionGeneration(
                id=generation_id,
                tenant_id=tenant_id,
                document_version_id=version_id,
                parser_version=1,
                chunker_version=1,
                embedding_version=1,
                embedding_model="evaluation-controlled",
                embedding_dimension=8,
                status=DocumentIngestionStatus.SUCCEEDED.value,
                stage=DocumentIngestionStage.READY.value,
                chunk_count=len(items),
                embedded_count=len(items),
                active=True,
                started_at=now,
                finished_at=now,
            )
        )
        await session.flush()
        for index, item in enumerate(items):
            session.add(
                DocumentChunk(
                    id=chunk_ids[item.key],
                    tenant_id=tenant_id,
                    document_version_id=version_id,
                    generation_id=generation_id,
                    chunk_index=index,
                    heading=item.key,
                    page_number=None,
                    start_offset=0,
                    end_offset=len(item.text),
                    normalized_text=item.text,
                    content_sha256=hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                    search_vector=func.to_tsvector("simple", item.text),
                    embedding=list(item.embedding),
                )
            )
    return version_id, chunk_ids


async def run_live_evaluation(dataset_path: Path) -> dict[str, Any]:
    dataset = load_live_dataset(dataset_path)
    dataset_bytes = await asyncio.to_thread(dataset_path.read_bytes)
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    tenant_id, actor_id = await _seed_identity(session_factory, dataset=dataset)
    try:
        version_id, chunk_ids = await _seed_corpus_version(
            session_factory,
            dataset=dataset,
            tenant_id=tenant_id,
            actor_id=actor_id,
            corpus_name="authorized",
            items=dataset.corpus,
        )
        if dataset.distractors:
            await _seed_corpus_version(
                session_factory,
                dataset=dataset,
                tenant_id=tenant_id,
                actor_id=actor_id,
                corpus_name="wrong-version",
                items=dataset.distractors,
            )
        provider = DatasetEmbeddingProvider(
            {
                case.query.strip(): case.query_embedding
                for case in dataset.cases
                if case.query.strip()
            }
        )
        service = HybridRetrievalService(
            session_factory=session_factory,
            embedding_provider=provider,
            top_k=dataset.top_k,
            rrf_k=dataset.rrf_k,
            min_candidates=dataset.min_candidates,
            max_vector_distance=dataset.max_vector_distance,
        )
        eval_cases: list[RetrievalEvalCase] = []
        case_results: list[dict[str, Any]] = []
        key_by_chunk_id = {chunk_id: key for key, chunk_id in chunk_ids.items()}
        for case in dataset.cases:
            decision = await service.retrieve(
                tenant_id=tenant_id,
                document_version_id=version_id,
                query=case.query,
            )
            retrieved_ids = tuple(str(candidate.chunk_id) for candidate in decision.candidates)
            relevant_ids = tuple(str(chunk_ids[key]) for key in case.relevant_chunk_keys)
            eval_cases.append(
                RetrievalEvalCase(
                    case_id=case.case_id,
                    relevant_chunk_ids=relevant_ids,
                    retrieved_chunk_ids=retrieved_ids,
                    expected_refusal=case.expected_refusal,
                    predicted_refusal=not decision.accepted,
                )
            )
            case_results.append(
                {
                    "case_id": case.case_id,
                    "accepted": decision.accepted,
                    "refusal_reason": (
                        decision.refusal_reason.value if decision.refusal_reason else None
                    ),
                    "retrieved_chunk_keys": [
                        key_by_chunk_id[candidate.chunk_id] for candidate in decision.candidates
                    ],
                    "retrieved_chunk_ids": retrieved_ids,
                    "scores": [candidate.score for candidate in decision.candidates],
                }
            )
        report = asdict(evaluate_retrieval_cases(tuple(eval_cases), k=dataset.k))
        report["citation_precision"] = None
        async with session_factory() as session:
            alembic_revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            postgres_version = await session.scalar(text("SELECT version()"))
            pgvector_version = await session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        return {
            "dataset_version": dataset.version,
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "limitations": dataset.limitations,
            "citation_evaluation": "not measured because M3 has no answer or citation generator",
            "runtime": {
                "alembic_revision": alembic_revision,
                "postgres_version": postgres_version,
                "pgvector_version": pgvector_version,
                "embedding_provider": "dataset-controlled",
                "embedding_dimension": 8,
                "top_k": dataset.top_k,
                "rrf_k": dataset.rrf_k,
                "min_score": service.min_score,
                "min_candidates": dataset.min_candidates,
                "max_vector_distance": dataset.max_vector_distance,
            },
            **report,
            "cases": case_results,
        }
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=Path("evaluation/m3_retrieval_live_v2.json"),
    )
    args = parser.parse_args()
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        report = runner.run(run_live_evaluation(args.dataset))
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
