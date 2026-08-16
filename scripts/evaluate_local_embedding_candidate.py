"""Evaluate an embedding candidate against the local synthetic RAG corpus.

This evaluator deliberately stops at vector retrieval.  It mirrors the
production parser/chunker/query instruction, but does not upload documents,
write to Postgres, or execute an Agent run.  API credentials are read from a
dotenv-style channel file and are never emitted in the report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import SecretStr

from enterprise_doc_core.config import EmbeddingProviderKind, EmbeddingSettings
from enterprise_doc_core.documents.embedding_provider import (
    OpenAICompatibleEmbeddingProvider,
)
from enterprise_doc_core.documents.ingestion import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    chunk_sections,
    parse_document_bytes,
)
from enterprise_doc_core.documents.retrieval_service import format_embedding_query
from enterprise_doc_core.evaluation.provenance import (
    capture_report_provenance,
    seal_report_payload,
)
from enterprise_doc_core.evaluation.rag_quality import (
    LoadedRagQualityDataset,
    RagExpectedOutcome,
    RagQualityCase,
    load_rag_quality_dataset,
)

DEFAULT_QUERY_INSTRUCTION = (
    "Given a user question about enterprise documents, retrieve relevant passages "
    "that answer the question"
)
DEFAULT_DIMENSION = 1024
DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 120
DEFAULT_MAX_VECTOR_DISTANCE = 0.65
REPORT_SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALIBRATION_THRESHOLDS = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65)


@dataclass(frozen=True, slots=True)
class LocalChunk:
    document_key: str
    chunk_index: int
    text: str
    chunk_id: str
    anchor_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk: LocalChunk
    score: float


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunk_id(document_key: str, chunk_index: int, text: str) -> str:
    return _sha256(f"{document_key}\0{chunk_index}\0{text}")[:16]


def _parse_channel_env(path: Path) -> dict[str, str]:
    """Read only simple KEY=VALUE dotenv entries without mutating os.environ."""

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"unable to read channel env file: {path}") from error
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ValueError(f"invalid channel env line {line_number}")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid channel env key on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _channel_fields(channel_env: dict[str, str], channel_name: str) -> tuple[str, str, str]:
    normalized = channel_name.strip()
    if normalized.casefold() == "free":
        keys = ("AI_PROVIDER_NAME", "AI_BASE_URL", "AI_API_KEY")
    else:
        match = re.fullmatch(r"Free([1-9][0-9]*)", normalized, flags=re.IGNORECASE)
        if match is None:
            raise ValueError("channel name must be Free or FreeN")
        suffix = match.group(1)
        keys = (f"PROVIDER_NAME{suffix}", f"BASE_URL{suffix}", f"API_KEY{suffix}")
    provider_name, base_url, api_key = (channel_env.get(key, "").strip() for key in keys)
    if not provider_name or not base_url or not api_key:
        raise ValueError(f"channel {normalized} is missing provider, base URL, or API key")
    if provider_name.casefold() != normalized.casefold():
        raise ValueError(f"channel env provider name mismatch for {normalized}")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("embedding channel base URL must be an HTTPS URL with a host")
    return provider_name, base_url.rstrip("/"), api_key


def _provider_secret_fields(path: Path) -> tuple[str, str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("unable to read provider secret JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("provider secret JSON must contain an object")
    values: list[str] = []
    for key in ("base_url", "model_name", "api_key"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"provider secret JSON is missing {key}")
        values.append(value.strip())
    base_url, model_name, api_key = values
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("embedding provider base URL must be an HTTPS URL with a host")
    return base_url.rstrip("/"), model_name, api_key


def _build_candidate_provider(
    *,
    channel_env: Path | None,
    provider_secret_json: Path | None = None,
    channel_name: str,
    model_name: str | None,
    dimension: int,
    version: int,
    timeout_seconds: float,
    batch_size: int,
) -> tuple[EmbeddingProvider, dict[str, object]]:
    if (channel_env is None) == (provider_secret_json is None):
        raise ValueError("select exactly one provider credential source")
    if provider_secret_json is not None:
        base_url, secret_model_name, api_key = _provider_secret_fields(provider_secret_json)
        if model_name is not None and model_name != secret_model_name:
            raise ValueError("--model must match model_name in provider secret JSON")
        provider_name = "reviewed-staging-route"
        resolved_model_name = secret_model_name
        credential_source = "provider_secret_json"
    else:
        assert channel_env is not None
        env_values = _parse_channel_env(channel_env)
        provider_name, base_url, api_key = _channel_fields(env_values, channel_name)
        resolved_model_name = model_name or "qwen3-embedding-8b"
        credential_source = "channel_env"
    settings = EmbeddingSettings(
        provider=EmbeddingProviderKind.OPENAI_COMPATIBLE,
        base_url=base_url,
        api_key=SecretStr(api_key),
        model_name=resolved_model_name,
        dimension=dimension,
        version=version,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
        max_retries=2,
        send_dimensions=True,
        query_instruction=DEFAULT_QUERY_INSTRUCTION,
    )
    host = urlparse(base_url).hostname
    return OpenAICompatibleEmbeddingProvider(settings=settings), {
        "provider": provider_name,
        "requested_model_name": resolved_model_name,
        "embedding_version": version,
        "dimension": dimension,
        "base_url_host": host,
        "credential_source": credential_source,
        "api_key_present": True,
    }


def _build_chunks(
    loaded: LoadedRagQualityDataset,
    *,
    max_chars: int,
    overlap_chars: int,
) -> dict[str, tuple[LocalChunk, ...]]:
    chunks_by_document: dict[str, tuple[LocalChunk, ...]] = {}
    anchors = loaded.dataset.anchors_by_id
    for document in loaded.dataset.documents:
        sections = parse_document_bytes(
            loaded.documents[document.document_key],
            extension=Path(document.path).suffix,
        )
        parsed = chunk_sections(sections, max_chars=max_chars, overlap_chars=overlap_chars)
        chunks: list[LocalChunk] = []
        for chunk in parsed:
            normalized_text = _normalize(chunk.text)
            anchor_ids = tuple(
                anchor_id
                for anchor_id in (anchor.anchor_id for anchor in document.anchors)
                if _normalize(anchors[anchor_id].quote) in normalized_text
            )
            chunks.append(
                LocalChunk(
                    document_key=document.document_key,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    chunk_id=_chunk_id(document.document_key, chunk.chunk_index, chunk.text),
                    anchor_ids=anchor_ids,
                )
            )
        if not chunks:
            raise ValueError(f"document produced no chunks: {document.document_key}")
        chunks_by_document[document.document_key] = tuple(chunks)
    return chunks_by_document


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must have equal, non-zero dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("vectors must have non-zero norms")
    return dot / (left_norm * right_norm)


def _rank_chunks(
    chunks: Sequence[LocalChunk],
    vectors: Sequence[Sequence[float]],
    query: Sequence[float],
) -> tuple[RankedChunk, ...]:
    ranked = [
        RankedChunk(chunk, _cosine(query, vector))
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    return tuple(sorted(ranked, key=lambda item: (-item.score, item.chunk.chunk_index)))


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be in [0, 100]")
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = index - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def _score_case(
    case: RagQualityCase,
    ranked: Sequence[RankedChunk],
    *,
    ks: Sequence[int],
    refusal_similarity_threshold: float,
) -> dict[str, object]:
    expected = set(case.expected_anchor_ids)
    anchor_ranks: dict[str, int] = {}
    for rank, item in enumerate(ranked, start=1):
        for anchor_id in item.chunk.anchor_ids:
            anchor_ranks.setdefault(anchor_id, rank)
    metrics: dict[str, float | None] = {}
    if case.expected_outcome is RagExpectedOutcome.ANSWER:
        for k in ks:
            found = sum(anchor_ranks.get(anchor_id, math.inf) <= k for anchor_id in expected)
            metrics[f"anchor_recall_at_{k}"] = found / len(expected) if expected else None
        first_rank = min(
            (anchor_ranks[anchor_id] for anchor_id in expected if anchor_id in anchor_ranks),
            default=None,
        )
        metrics["mrr"] = 1.0 / first_rank if first_rank is not None else 0.0
    else:
        top_score = ranked[0].score if ranked else None
        metrics["top1_similarity"] = top_score
        metrics["vector_candidate"] = (
            float(top_score >= refusal_similarity_threshold) if top_score is not None else 0.0
        )
    return {
        "case_id": case.case_id,
        "category": case.category.value,
        "query_sha256": _sha256(case.query),
        "expected_anchor_ids": sorted(expected),
        "metrics": metrics,
        "anchor_ranks": anchor_ranks,
        "top_chunks": [
            {
                "rank": rank,
                "chunk_id": item.chunk.chunk_id,
                "anchor_ids": list(item.chunk.anchor_ids),
                "cosine_similarity": round(item.score, 6),
            }
            for rank, item in enumerate(ranked[: max(ks, default=10)], start=1)
        ],
    }


def _aggregate_case_results(
    results: Sequence[dict[str, object]], *, ks: Sequence[int]
) -> dict[str, float | int | None]:
    def metrics_of(result: dict[str, object]) -> dict[str, float | None]:
        raw_metrics = result.get("metrics")
        if not isinstance(raw_metrics, dict):
            raise ValueError("case result metrics must be a mapping")
        metrics: dict[str, float | None] = {}
        for key, value in raw_metrics.items():
            if not isinstance(key, str) or (
                value is not None
                and (not isinstance(value, (int, float)) or isinstance(value, bool))
            ):
                raise ValueError("case result metrics contain an invalid value")
            metrics[key] = None if value is None else float(value)
        return metrics

    answer_results = [result for result in results if metrics_of(result).get("mrr") is not None]
    refusal_results = [result for result in results if "top1_similarity" in metrics_of(result)]
    metrics: dict[str, float | int | None] = {
        "case_count": len(results),
        "answer_case_count": len(answer_results),
        "refusal_case_count": len(refusal_results),
    }
    for k in ks:
        values = [float(metrics_of(result)[f"anchor_recall_at_{k}"]) for result in answer_results]
        metrics[f"answer_anchor_recall_at_{k}"] = (
            round(statistics.fmean(values), 6) if values else None
        )
    mrr_values = [float(metrics_of(result)["mrr"]) for result in answer_results]
    top_values = [float(metrics_of(result)["top1_similarity"]) for result in refusal_results]
    candidate_values = [float(metrics_of(result)["vector_candidate"]) for result in refusal_results]
    metrics["answer_mrr"] = round(statistics.fmean(mrr_values), 6) if mrr_values else None
    metrics["refusal_top1_similarity_mean"] = (
        round(statistics.fmean(top_values), 6) if top_values else None
    )
    metrics["refusal_top1_similarity_p95"] = _percentile(top_values, 95)
    metrics["refusal_top1_similarity_max"] = round(max(top_values), 6) if top_values else None
    metrics["refusal_vector_candidate_rate"] = (
        round(statistics.fmean(candidate_values), 6) if candidate_values else None
    )
    return metrics


def _similarity_calibration(
    ranked_cases: Sequence[tuple[RagQualityCase, Sequence[RankedChunk]]],
    *,
    thresholds: Sequence[float],
) -> list[dict[str, float]]:
    answer_cases = [
        item for item in ranked_cases if item[0].expected_outcome is RagExpectedOutcome.ANSWER
    ]
    refusal_cases = [
        item for item in ranked_cases if item[0].expected_outcome is RagExpectedOutcome.REFUSAL
    ]
    rows: list[dict[str, float]] = []
    for threshold in thresholds:
        answer_recalls: list[float] = []
        answer_complete: list[float] = []
        for case, ranked in answer_cases:
            expected = set(case.expected_anchor_ids)
            eligible = {
                anchor_id
                for item in ranked
                if item.score >= threshold
                for anchor_id in item.chunk.anchor_ids
            }
            matched = len(expected.intersection(eligible))
            answer_recalls.append(matched / len(expected))
            answer_complete.append(float(matched == len(expected)))
        refusal_candidates = [
            float(bool(ranked) and ranked[0].score >= threshold) for _, ranked in refusal_cases
        ]
        rows.append(
            {
                "similarity_threshold": threshold,
                "answer_anchor_recall": round(statistics.fmean(answer_recalls), 6),
                "answer_complete_anchor_rate": round(statistics.fmean(answer_complete), 6),
                "refusal_vector_candidate_rate": round(statistics.fmean(refusal_candidates), 6),
            }
        )
    return rows


async def _evaluate_provider(
    provider: EmbeddingProvider,
    *,
    cases: Sequence[RagQualityCase],
    chunks_by_document: dict[str, tuple[LocalChunk, ...]],
    query_instruction: str,
    ks: Sequence[int],
    refusal_similarity_threshold: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    all_chunks = [chunk for chunks in chunks_by_document.values() for chunk in chunks]
    chunk_vectors = await provider.embed(tuple(chunk.text for chunk in all_chunks))
    if len(chunk_vectors) != len(all_chunks):
        raise ValueError("embedding provider returned an invalid chunk batch")
    vectors_by_document: dict[str, tuple[tuple[float, ...], ...]] = {}
    cursor = 0
    for document_key, chunks in chunks_by_document.items():
        vectors_by_document[document_key] = tuple(chunk_vectors[cursor : cursor + len(chunks)])
        cursor += len(chunks)
    query_texts = tuple(format_embedding_query(case.query, query_instruction) for case in cases)
    query_vectors = await provider.embed(query_texts)
    if len(query_vectors) != len(cases):
        raise ValueError("embedding provider returned an invalid query batch")
    results: list[dict[str, object]] = []
    ranked_cases: list[tuple[RagQualityCase, Sequence[RankedChunk]]] = []
    for case, query_vector in zip(cases, query_vectors, strict=True):
        chunks = chunks_by_document[case.document_key]
        ranked = _rank_chunks(chunks, vectors_by_document[case.document_key], query_vector)
        ranked_cases.append((case, ranked))
        results.append(
            _score_case(
                case,
                ranked,
                ks=ks,
                refusal_similarity_threshold=refusal_similarity_threshold,
            )
        )
    return (
        {
            "chunk_count": len(all_chunks),
            "query_count": len(cases),
            "vector_dimension": len(chunk_vectors[0]) if chunk_vectors else None,
            "metrics": _aggregate_case_results(results, ks=ks),
            "similarity_calibration": _similarity_calibration(
                ranked_cases,
                thresholds=DEFAULT_CALIBRATION_THRESHOLDS,
            ),
        },
        results,
    )


def _delta(candidate: dict[str, object], baseline: dict[str, object]) -> dict[str, float]:
    candidate_metrics = candidate["metrics"]
    baseline_metrics = baseline["metrics"]
    assert isinstance(candidate_metrics, dict)
    assert isinstance(baseline_metrics, dict)
    output: dict[str, float] = {}
    for key, value in candidate_metrics.items():
        base = baseline_metrics.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isinstance(base, (int, float))
            and not isinstance(base, bool)
        ):
            output[key] = round(float(value) - float(base), 6)
    return output


async def run_evaluation(
    *,
    dataset_path: Path,
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
    loaded = load_rag_quality_dataset(dataset_path)
    chunks_by_document = _build_chunks(
        loaded,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    cases = loaded.dataset.cases
    candidate, route = _build_candidate_provider(
        channel_env=channel_env,
        provider_secret_json=provider_secret_json,
        channel_name=channel_name,
        model_name=model_name,
        dimension=dimension,
        version=embedding_version,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
    )
    baseline: EmbeddingProvider = HashEmbeddingProvider(dimension=dimension)
    refusal_threshold = 1.0 - DEFAULT_MAX_VECTOR_DISTANCE
    baseline_summary, baseline_cases = await _evaluate_provider(
        baseline,
        cases=cases,
        chunks_by_document=chunks_by_document,
        query_instruction=DEFAULT_QUERY_INSTRUCTION,
        ks=ks,
        refusal_similarity_threshold=refusal_threshold,
    )
    candidate_summary, candidate_cases = await _evaluate_provider(
        candidate,
        cases=cases,
        chunks_by_document=chunks_by_document,
        query_instruction=DEFAULT_QUERY_INSTRUCTION,
        ks=ks,
        refusal_similarity_threshold=refusal_threshold,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite": "local-embedding-candidate-evaluation",
        "status": "completed",
        "dataset_version": loaded.dataset.version,
        "dataset_sha256": loaded.dataset_sha256,
        "corpus_sha256": loaded.corpus_sha256,
        "coverage": {
            "case_count": len(cases),
            "document_count": len(loaded.dataset.documents),
            "category_counts": {
                category.value: sum(case.category is category for case in cases)
                for category in type(cases[0].category)
            },
        },
        "retrieval_contract": {
            "chunk_max_chars": max_chars,
            "chunk_overlap_chars": overlap_chars,
            "query_instruction_sha256": _sha256(DEFAULT_QUERY_INSTRUCTION),
            "query_instruction": DEFAULT_QUERY_INSTRUCTION,
            "vector_dimension": dimension,
            "refusal_vector_candidate_similarity_threshold": refusal_threshold,
            "ks": list(ks),
        },
        "baseline": {
            "provider": "hash",
            "requested_model_name": "hash-sha256-v1",
            "embedding_version": 2,
            **baseline_summary,
            "cases": baseline_cases,
        },
        "candidate": {
            **route,
            **candidate_summary,
            "cases": candidate_cases,
        },
        "candidate_delta_vs_hash": _delta(candidate_summary, baseline_summary),
        "limitations": [
            (
                "This evaluates vector retrieval only; it does not execute keyword recall, "
                "RRF, Agent answering, or citation validation."
            ),
            (
                "This report compares the selected route with the deterministic hash baseline; "
                "the separate Free 8B report is the reference for cross-model comparison."
            ),
            "The corpus is synthetic and contains no customer or personal data.",
            (
                "A version 3 reindex remains blocked until this candidate passes a reviewed "
                "quality decision and the full staging reindex gate."
            ),
        ],
        "generated_at": datetime.now(UTC).isoformat(),
        "values_redacted": True,
    }


def _report_command(args: argparse.Namespace, ks: Sequence[int]) -> list[str]:
    command = [
        "python",
        "scripts/evaluate_local_embedding_candidate.py",
        "--dataset",
        "<dataset>",
    ]
    if args.provider_secret_json:
        command.extend(["--provider-secret-json", "<provider-secret-json>"])
    else:
        command.extend(
            [
                "--channel-env",
                "<channel-env>",
                "--channel-name",
                str(args.channel_name),
            ]
        )
    if args.model_name:
        command.extend(["--model", str(args.model_name)])
    command.extend(
        [
            "--embedding-version",
            str(args.embedding_version),
            "--dimension",
            str(args.dimension),
            "--max-chars",
            str(args.max_chars),
            "--overlap-chars",
            str(args.overlap_chars),
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--batch-size",
            str(args.batch_size),
        ]
    )
    for k in ks:
        command.extend(["--k", str(k)])
    if args.report_path:
        command.extend(["--report-path", "<report-path>"])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/rag_quality_v2.json"))
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument(
        "--channel-env",
        type=Path,
        help="dotenv file containing the selected Free channel (the API key is not reported)",
    )
    credentials.add_argument(
        "--provider-secret-json",
        type=Path,
        help="operator-owned provider JSON with base_url, model_name, and api_key",
    )
    parser.add_argument("--channel-name", default="Free")
    parser.add_argument("--model", dest="model_name")
    parser.add_argument("--embedding-version", type=int, default=3)
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIMENSION)
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
    if args.dimension != DEFAULT_DIMENSION:
        parser.error(f"--dimension must be {DEFAULT_DIMENSION}")
    if args.overlap_chars < 0 or args.overlap_chars >= args.max_chars:
        parser.error("--overlap-chars must be in [0, --max-chars)")
    if args.embedding_version < 2:
        parser.error("--embedding-version must be >= 2")
    if args.timeout_seconds <= 0 or args.batch_size <= 0:
        parser.error("timeout and batch size must be positive")
    try:
        report = asyncio.run(
            run_evaluation(
                dataset_path=args.dataset,
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
    candidate = report.get("candidate")
    if not isinstance(candidate, dict):
        raise SystemExit("candidate report section is invalid")
    provenance_input = json.dumps(
        {
            "dataset_sha256": report["dataset_sha256"],
            "corpus_sha256": report["corpus_sha256"],
            "retrieval_contract": report["retrieval_contract"],
            "candidate_route": {
                key: candidate[key]
                for key in (
                    "provider",
                    "requested_model_name",
                    "embedding_version",
                    "dimension",
                    "base_url_host",
                )
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    report["provenance"] = capture_report_provenance(
        command=_report_command(args, ks),
        root=ROOT,
        execution_scope="local-real-provider-vector-quality",
        input_sha256=_sha256(provenance_input),
    ).model_dump(mode="json")
    sealed = seal_report_payload(report)
    rendered = json.dumps(sealed, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
