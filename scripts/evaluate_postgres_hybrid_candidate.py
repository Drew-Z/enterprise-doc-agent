"""Evaluate a real embedding route through PostgreSQL ts_rank_cd and pgvector.

The evaluator seeds an isolated synthetic tenant, document versions, and one
active ingestion generation per corpus document.  It then calls the production
HybridRetrievalService, so keyword ranking and candidate fusion use the same
PostgreSQL path as the application.  The seeded tenant is deleted before the
process exits; credentials and embedding values are never written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

if __package__:
    from .evaluate_local_embedding_candidate import (
        DEFAULT_MAX_CHARS,
        DEFAULT_MAX_VECTOR_DISTANCE,
        DEFAULT_OVERLAP_CHARS,
        DEFAULT_QUERY_INSTRUCTION,
        DEFAULT_RRF_K,
        DEFAULT_TOP_K,
        LocalChunk,
        _build_candidate_provider,
        _build_chunks,
        _sha256,
    )
else:
    from evaluate_local_embedding_candidate import (
        DEFAULT_MAX_CHARS,
        DEFAULT_MAX_VECTOR_DISTANCE,
        DEFAULT_OVERLAP_CHARS,
        DEFAULT_QUERY_INSTRUCTION,
        DEFAULT_RRF_K,
        DEFAULT_TOP_K,
        LocalChunk,
        _build_candidate_provider,
        _build_chunks,
        _sha256,
    )
from pydantic import SecretStr
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents.models import (
    DEFAULT_EMBEDDING_DIMENSION,
    Document,
    DocumentChunk,
    DocumentIngestionGeneration,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentVersion,
    DocumentVersionStatus,
)
from enterprise_doc_core.documents.retrieval_service import (
    HybridRetrievalService,
    format_embedding_query,
)
from enterprise_doc_core.evaluation.provenance import capture_report_provenance, seal_report_payload
from enterprise_doc_core.evaluation.rag_quality import (
    LoadedRagQualityDataset,
    RagExpectedOutcome,
    RagQualityCase,
    load_rag_quality_dataset,
)
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.uploads.models import UploadSession, UploadSessionStatus

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SeededDocument:
    version_id: UUID
    chunk_ids: dict[UUID, LocalChunk]


@dataclass(frozen=True, slots=True)
class SeededCorpus:
    tenant_id: UUID
    documents: dict[str, SeededDocument]


class PrecomputedQueryEmbeddingProvider:
    def __init__(self, vectors: dict[str, Sequence[float]]) -> None:
        self.vectors = vectors

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        try:
            return tuple(tuple(self.vectors[text]) for text in texts)
        except KeyError as error:
            raise ValueError("retrieval requested an unexpected query") from error


async def _seed_corpus(
    session_factory: async_sessionmaker[AsyncSession],
    loaded: LoadedRagQualityDataset,
    chunks_by_document: dict[str, tuple[LocalChunk, ...]],
    vectors_by_document: dict[str, tuple[Sequence[float], ...]],
    *,
    embedding_model: str,
    embedding_version: int,
) -> SeededCorpus:
    tenant_id = uuid4()
    actor_id = uuid4()
    suffix = uuid4().hex
    now = datetime.now(UTC)
    seeded: dict[str, SeededDocument] = {}
    async with session_factory.begin() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"Postgres hybrid evaluation {suffix}",
                slug=f"postgres-hybrid-eval-{suffix}",
                quota_bytes=1024 * 1024 * 1024,
            )
        )
        session.add(User(id=actor_id, email=f"postgres-hybrid-eval-{suffix}@example.test"))
        await session.flush()
        for document in loaded.dataset.documents:
            document_key = document.document_key
            chunks = chunks_by_document[document_key]
            vectors = vectors_by_document[document_key]
            if len(chunks) != len(vectors):
                raise ValueError(f"embedding count mismatch for {document_key}")
            document_id = uuid4()
            version_id = uuid4()
            upload_id = uuid4()
            filename = Path(document.path).name
            content = loaded.documents[document_key]
            content_sha256 = hashlib.sha256(content).hexdigest()
            object_key = f"{tenant_id}/documents/{version_id}/{filename}"
            session.add(
                UploadSession(
                    id=upload_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    pending_document_id=document_id,
                    pending_version_id=version_id,
                    status=UploadSessionStatus.COMPLETED.value,
                    idempotency_key=f"postgres-hybrid-eval:{suffix}:{document_key}",
                    request_fingerprint=content_sha256,
                    object_key=object_key,
                    original_filename=filename,
                    extension=Path(filename).suffix,
                    declared_media_type="text/plain",
                    size_bytes=len(content),
                    declared_sha256=content_sha256,
                    part_size_bytes=len(content),
                    expected_part_count=1,
                    reserved_bytes=0,
                    expires_at=now + timedelta(hours=1),
                    completed_at=now,
                )
            )
            session.add(
                Document(id=document_id, tenant_id=tenant_id, created_by=actor_id, title=filename)
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
                    object_key=object_key,
                    original_filename=filename,
                    declared_media_type="text/plain",
                    detected_media_type="text/plain",
                    size_bytes=len(content),
                    declared_sha256=content_sha256,
                    created_by=actor_id,
                )
            )
            await session.flush()
            upload = await session.get(UploadSession, upload_id)
            if upload is None:
                raise RuntimeError("seeded upload session was not persisted")
            upload.document_version_id = version_id
            generation_id = uuid4()
            session.add(
                DocumentIngestionGeneration(
                    id=generation_id,
                    tenant_id=tenant_id,
                    document_version_id=version_id,
                    parser_version=1,
                    chunker_version=1,
                    embedding_version=embedding_version,
                    embedding_model=embedding_model,
                    embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
                    status=DocumentIngestionStatus.SUCCEEDED.value,
                    stage=DocumentIngestionStage.READY.value,
                    chunk_count=len(chunks),
                    embedded_count=len(chunks),
                    active=True,
                    started_at=now,
                    finished_at=now,
                )
            )
            await session.flush()
            chunk_ids: dict[UUID, LocalChunk] = {}
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk_uuid = uuid4()
                chunk_ids[chunk_uuid] = chunk
                session.add(
                    DocumentChunk(
                        id=chunk_uuid,
                        tenant_id=tenant_id,
                        document_version_id=version_id,
                        generation_id=generation_id,
                        chunk_index=chunk.chunk_index,
                        heading=None,
                        page_number=None,
                        start_offset=0,
                        end_offset=len(chunk.text),
                        normalized_text=chunk.text,
                        content_sha256=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                        search_vector=func.to_tsvector("simple", chunk.text),
                        embedding=list(vector),
                    )
                )
            seeded[document_key] = SeededDocument(version_id=version_id, chunk_ids=chunk_ids)
    return SeededCorpus(tenant_id=tenant_id, documents=seeded)


def _case_result(
    case: RagQualityCase,
    decision: Any,
    seeded_document: SeededDocument,
    *,
    ks: Sequence[int],
) -> dict[str, object]:
    anchor_ranks: dict[str, int] = {}
    top_chunks: list[dict[str, object]] = []
    for rank, candidate in enumerate(decision.candidates, start=1):
        local_chunk = seeded_document.chunk_ids.get(candidate.chunk_id)
        if local_chunk is None:
            raise ValueError("retrieval returned a chunk outside the seeded document")
        for anchor_id in local_chunk.anchor_ids:
            anchor_ranks.setdefault(anchor_id, rank)
        top_chunks.append(
            {
                "rank": rank,
                "chunk_id": local_chunk.chunk_id,
                "anchor_ids": list(local_chunk.anchor_ids),
                "score": round(candidate.score, 6),
            }
        )
    expected = set(case.expected_anchor_ids)
    metrics: dict[str, object] = {}
    if case.expected_outcome is RagExpectedOutcome.ANSWER:
        for k in ks:
            found = sum(anchor_ranks.get(anchor_id, float("inf")) <= k for anchor_id in expected)
            metrics[f"anchor_recall_at_{k}"] = found / len(expected) if expected else None
        first_rank = min(
            (anchor_ranks[anchor_id] for anchor_id in expected if anchor_id in anchor_ranks),
            default=None,
        )
        metrics["mrr"] = 1.0 / first_rank if first_rank is not None else 0.0
    else:
        metrics["accepted"] = bool(decision.accepted)
        metrics["top1_score"] = decision.candidates[0].score if decision.candidates else None
    return {
        "case_id": case.case_id,
        "category": case.category.value,
        "query_sha256": _sha256(case.query),
        "expected_anchor_ids": sorted(expected),
        "accepted": decision.accepted,
        "refusal_reason": decision.refusal_reason.value if decision.refusal_reason else None,
        "metrics": metrics,
        "anchor_ranks": anchor_ranks,
        "top_chunks": top_chunks,
    }


def _result_metrics(result: dict[str, object]) -> dict[str, Any]:
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("case result metrics must be a mapping")
    return metrics


def _aggregate(
    results: Sequence[dict[str, object]], *, ks: Sequence[int]
) -> dict[str, float | int | None]:
    answer = [r for r in results if "mrr" in _result_metrics(r)]
    refusal = [r for r in results if "accepted" in _result_metrics(r)]
    metrics: dict[str, float | int | None] = {
        "case_count": len(results),
        "answer_case_count": len(answer),
        "refusal_case_count": len(refusal),
    }
    for k in ks:
        values = [float(_result_metrics(r)[f"anchor_recall_at_{k}"]) for r in answer]
        metrics[f"answer_anchor_recall_at_{k}"] = sum(values) / len(values) if values else None
    mrr = [float(_result_metrics(r)["mrr"]) for r in answer]
    accepted = [float(_result_metrics(r)["accepted"]) for r in refusal]
    scores = [
        float(_result_metrics(r)["top1_score"])
        for r in refusal
        if _result_metrics(r)["top1_score"] is not None
    ]
    metrics["answer_mrr"] = sum(mrr) / len(mrr) if mrr else None
    metrics["refusal_acceptance_rate"] = sum(accepted) / len(accepted) if accepted else None
    metrics["refusal_top1_score_max"] = max(scores) if scores else None
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in metrics.items()
    }


async def run_evaluation(
    *,
    dataset_path: Path,
    database_url: str,
    channel_env: Path | None,
    provider_secret_json: Path | None,
    channel_name: str,
    model_name: str | None,
    embedding_version: int,
    max_chars: int,
    overlap_chars: int,
    dimension: int,
    timeout_seconds: float,
    batch_size: int,
    ks: Sequence[int],
) -> dict[str, object]:
    if dimension != DEFAULT_EMBEDDING_DIMENSION:
        raise ValueError(f"dimension must be {DEFAULT_EMBEDDING_DIMENSION}")
    loaded = load_rag_quality_dataset(dataset_path)
    chunks_by_document = _build_chunks(loaded, max_chars=max_chars, overlap_chars=overlap_chars)
    provider, route = _build_candidate_provider(
        channel_env=channel_env,
        provider_secret_json=provider_secret_json,
        channel_name=channel_name,
        model_name=model_name,
        dimension=dimension,
        version=embedding_version,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
    )
    all_chunks = [chunk for chunks in chunks_by_document.values() for chunk in chunks]
    vectors = await provider.embed(tuple(chunk.text for chunk in all_chunks))
    if len(vectors) != len(all_chunks):
        raise ValueError("embedding provider returned an invalid chunk batch")
    vectors_by_document: dict[str, tuple[Sequence[float], ...]] = {}
    cursor = 0
    for document_key, chunks in chunks_by_document.items():
        vectors_by_document[document_key] = tuple(vectors[cursor : cursor + len(chunks)])
        cursor += len(chunks)
    query_texts = tuple(
        format_embedding_query(case.query, DEFAULT_QUERY_INSTRUCTION)
        for case in loaded.dataset.cases
    )
    query_vectors = await provider.embed(query_texts)
    if len(query_vectors) != len(loaded.dataset.cases):
        raise ValueError("embedding provider returned an invalid query batch")
    query_provider = PrecomputedQueryEmbeddingProvider(
        dict(zip(query_texts, query_vectors, strict=True))
    )
    settings = DatabaseSettings(url=SecretStr(database_url))
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    seeded = await _seed_corpus(
        session_factory,
        loaded,
        chunks_by_document,
        vectors_by_document,
        embedding_model=str(route["requested_model_name"]),
        embedding_version=embedding_version,
    )
    try:
        results: dict[str, list[dict[str, object]]] = {}
        for gate_name, require_vector_evidence in (("default", False), ("vector_evidence", True)):
            service = HybridRetrievalService(
                session_factory=session_factory,
                embedding_provider=query_provider,
                embedding_model=str(route["requested_model_name"]),
                embedding_dimension=dimension,
                query_instruction=DEFAULT_QUERY_INSTRUCTION,
                top_k=DEFAULT_TOP_K,
                rrf_k=DEFAULT_RRF_K,
                max_vector_distance=DEFAULT_MAX_VECTOR_DISTANCE,
                require_vector_evidence=require_vector_evidence,
            )
            case_results: list[dict[str, object]] = []
            for case in loaded.dataset.cases:
                seeded_document = seeded.documents[case.document_key]
                decision = await service.retrieve(
                    tenant_id=seeded.tenant_id,
                    document_version_id=seeded_document.version_id,
                    query=case.query,
                )
                case_results.append(_case_result(case, decision, seeded_document, ks=ks))
            results[gate_name] = case_results
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "suite": "postgres-hybrid-embedding-candidate-evaluation",
            "status": "completed",
            "dataset_version": loaded.dataset.version,
            "dataset_sha256": loaded.dataset_sha256,
            "corpus_sha256": loaded.corpus_sha256,
            "coverage": {
                "case_count": len(loaded.dataset.cases),
                "document_count": len(loaded.dataset.documents),
            },
            "retrieval_contract": {
                "keyword_language": "simple",
                "keyword_rank": "ts_rank_cd",
                "chunk_max_chars": max_chars,
                "chunk_overlap_chars": overlap_chars,
                "vector_dimension": dimension,
                "max_vector_distance": DEFAULT_MAX_VECTOR_DISTANCE,
                "hybrid_top_k": DEFAULT_TOP_K,
                "hybrid_rrf_k": DEFAULT_RRF_K,
                "hybrid_min_score": 1.0 / (DEFAULT_RRF_K + 1),
                "query_instruction_sha256": _sha256(DEFAULT_QUERY_INSTRUCTION),
                "ks": list(ks),
            },
            "provider": route,
            "gates": {
                name: {"metrics": _aggregate(case_results, ks=ks), "cases": case_results}
                for name, case_results in results.items()
            },
            "limitations": [
                "The corpus is synthetic and contains no customer or personal data.",
                (
                    "This report measures retrieval only; Agent answering and citation validation "
                    "are not executed."
                ),
                (
                    "Real provider chunk and query vectors are generated once; the production "
                    "service reuses the precomputed query vectors while executing retrieval."
                ),
                (
                    "This local database run does not mutate staging and does not perform a "
                    "production reindex."
                ),
            ],
            "generated_at": datetime.now(UTC).isoformat(),
            "values_redacted": True,
        }
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == seeded.tenant_id))
        await engine.dispose()


def _report_command(args: argparse.Namespace, ks: Sequence[int]) -> list[str]:
    command = [
        "python",
        "scripts/evaluate_postgres_hybrid_candidate.py",
        "--dataset",
        "<dataset>",
        "--database-url",
        "<database-url>",
    ]
    if args.provider_secret_json:
        command.extend(["--provider-secret-json", "<provider-secret-json>"])
    else:
        command.extend(["--channel-env", "<channel-env>", "--channel-name", str(args.channel_name)])
    if args.model_name:
        command.extend(["--model", str(args.model_name)])
    command.extend(
        ["--embedding-version", str(args.embedding_version), "--dimension", str(args.dimension)]
    )
    for k in ks:
        command.extend(["--k", str(k)])
    if args.report_path:
        command.extend(["--report-path", "<report-path>"])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/rag_quality_v2.json"))
    parser.add_argument("--database-url", required=True, help="isolated PostgreSQL URL")
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--channel-env", type=Path)
    credentials.add_argument("--provider-secret-json", type=Path)
    parser.add_argument("--channel-name", default="Free")
    parser.add_argument("--model", dest="model_name")
    parser.add_argument("--embedding-version", type=int, default=3)
    parser.add_argument("--dimension", type=int, default=DEFAULT_EMBEDDING_DIMENSION)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--k", dest="ks", type=int, action="append")
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    ks = tuple(sorted(set(args.ks or [1, 3, 5, 10])))
    if not ks or any(k <= 0 for k in ks):
        parser.error("--k values must be positive")
    if args.max_chars <= 0 or args.overlap_chars < 0 or args.overlap_chars >= args.max_chars:
        parser.error("chunk size arguments are invalid")
    if args.embedding_version < 2 or args.timeout_seconds <= 0 or args.batch_size <= 0:
        parser.error("embedding version, timeout, and batch size must be positive")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        report = asyncio.run(
            run_evaluation(
                dataset_path=args.dataset,
                database_url=args.database_url,
                channel_env=args.channel_env,
                provider_secret_json=args.provider_secret_json,
                channel_name=args.channel_name,
                model_name=args.model_name,
                embedding_version=args.embedding_version,
                max_chars=args.max_chars,
                overlap_chars=args.overlap_chars,
                dimension=args.dimension,
                timeout_seconds=args.timeout_seconds,
                batch_size=args.batch_size,
                ks=ks,
            )
        )
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from error
    report["provenance"] = capture_report_provenance(
        command=_report_command(args, ks),
        root=ROOT,
        execution_scope="local-postgres-real-provider-hybrid-quality",
        input_sha256=_sha256(
            json.dumps(
                {
                    "dataset_sha256": report["dataset_sha256"],
                    "corpus_sha256": report["corpus_sha256"],
                    "retrieval_contract": report["retrieval_contract"],
                    "provider": report["provider"],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    ).model_dump(mode="json")
    rendered = (
        json.dumps(seal_report_payload(report), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    )
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
