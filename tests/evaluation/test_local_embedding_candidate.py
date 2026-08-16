from __future__ import annotations

import json
from pathlib import Path

import pytest
import scripts.evaluate_local_embedding_candidate as evaluator

from enterprise_doc_core.evaluation.rag_quality import load_rag_quality_dataset


def _dataset_path() -> Path:
    return Path(__file__).parents[2] / "evaluation" / "rag_quality_v2.json"


def test_channel_env_selects_free_without_returning_other_channels(tmp_path: Path) -> None:
    path = tmp_path / "channels.env"
    path.write_text(
        "AI_PROVIDER_NAME=Free\n"
        "AI_BASE_URL=https://embedding.example/v1\n"
        "AI_API_KEY=secret-free\n"
        "PROVIDER_NAME7=Free7\n"
        "BASE_URL7=https://other.example/v1\n"
        "API_KEY7=secret-other\n",
        encoding="utf-8",
    )

    values = evaluator._parse_channel_env(path)
    assert evaluator._channel_fields(values, "Free") == (
        "Free",
        "https://embedding.example/v1",
        "secret-free",
    )
    assert evaluator._channel_fields(values, "Free7")[1] == "https://other.example/v1"


def test_build_chunks_reuses_production_chunk_contract() -> None:
    loaded = load_rag_quality_dataset(_dataset_path())

    chunks = evaluator._build_chunks(loaded, max_chars=1200, overlap_chars=120)

    assert set(chunks) == {document.document_key for document in loaded.dataset.documents}
    assert all(chunk.chunk_id and chunk.text for items in chunks.values() for chunk in items)
    assert any("proc.po" in chunk.anchor_ids for chunk in chunks["procurement-policy"])


def test_score_case_reports_anchor_recall_and_mrr() -> None:
    loaded = load_rag_quality_dataset(_dataset_path())
    case = loaded.dataset.cases_by_id["hard-support-objectives"]
    chunks = evaluator._build_chunks(loaded, max_chars=1200, overlap_chars=120)
    document_chunks = chunks[case.document_key]
    target = next(chunk for chunk in document_chunks if "support.rto" in chunk.anchor_ids)
    other = next(chunk for chunk in document_chunks if chunk is not target)
    ranked = (
        evaluator.RankedChunk(other, 0.99),
        evaluator.RankedChunk(target, 0.90),
    )

    result = evaluator._score_case(
        case,
        ranked,
        ks=(1, 3),
        refusal_similarity_threshold=0.35,
    )

    assert result["metrics"] == {"anchor_recall_at_1": 0.0, "anchor_recall_at_3": 1.0, "mrr": 0.5}
    anchor_ranks = result["anchor_ranks"]
    assert isinstance(anchor_ranks, dict)
    assert anchor_ranks["support.rto"] == 2


def test_candidate_route_metadata_does_not_contain_api_key(tmp_path: Path) -> None:
    path = tmp_path / "channels.env"
    path.write_text(
        "AI_PROVIDER_NAME=Free\nAI_BASE_URL=https://embedding.example/v1\nAI_API_KEY=secret-free\n",
        encoding="utf-8",
    )

    _, route = evaluator._build_candidate_provider(
        channel_env=path,
        channel_name="Free",
        model_name="qwen3-embedding-8b",
        dimension=1024,
        version=3,
        timeout_seconds=20,
        batch_size=8,
    )

    rendered = json.dumps(route, sort_keys=True)

    assert route["api_key_present"] is True
    assert route["base_url_host"] == "embedding.example"
    assert "secret-free" not in rendered


def test_similarity_calibration_exposes_refusal_tradeoff() -> None:
    loaded = load_rag_quality_dataset(_dataset_path())
    chunks = evaluator._build_chunks(loaded, max_chars=1200, overlap_chars=120)
    answer = loaded.dataset.cases_by_id["fact-proc-manager"]
    refusal = loaded.dataset.cases_by_id["refuse-security-ceo"]
    answer_target = next(
        chunk for chunk in chunks[answer.document_key] if "proc.manager" in chunk.anchor_ids
    )
    refusal_candidate = chunks[refusal.document_key][0]

    rows = evaluator._similarity_calibration(
        (
            (answer, (evaluator.RankedChunk(answer_target, 0.60),)),
            (refusal, (evaluator.RankedChunk(refusal_candidate, 0.40),)),
        ),
        thresholds=(0.35, 0.55),
    )

    assert rows[0]["answer_complete_anchor_rate"] == 1.0
    assert rows[0]["refusal_vector_candidate_rate"] == 1.0
    assert rows[1]["answer_complete_anchor_rate"] == 1.0
    assert rows[1]["refusal_vector_candidate_rate"] == 0.0


def test_channel_env_rejects_http_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "channels.env"
    path.write_text(
        "AI_PROVIDER_NAME=Free\nAI_BASE_URL=http://embedding.example/v1\nAI_API_KEY=secret\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="HTTPS"):
        evaluator._channel_fields(evaluator._parse_channel_env(path), "Free")
