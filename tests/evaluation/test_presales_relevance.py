import copy

import pytest
from scripts.evaluate_presales_relevance import fingerprint, review, validate_scores


@pytest.mark.parametrize("change", ["query", "text", "id", "case", "duplicate"])
def test_relevance_review_rejects_changed_inputs_and_duplicate_scores(change):
    case = {"key": "q1", "query": "政策是什么\uff1f", "passages": [{"id": "c1", "text": "原文。"}]}
    case["input_fingerprint"] = fingerprint({"query": case["query"], "passages": case["passages"]})
    plan = {"cases": [case]}
    report = {
        "plan_fingerprint": fingerprint(plan),
        "cases": {
            "q1": {
                "input_fingerprint": case["input_fingerprint"],
                "scores": [{"id": "c1", "score": 0.5}],
            }
        },
    }
    validate_scores(plan, report)
    changed = copy.deepcopy(plan)
    if change == "query":
        changed["cases"][0]["query"] = "其他问题。"
    elif change in {"text", "id"}:
        changed["cases"][0]["passages"][0][change] = "altered"
    elif change == "case":
        report["cases"]["extra"] = report["cases"]["q1"]
    else:
        report["cases"]["q1"]["scores"] *= 2
    with pytest.raises(ValueError):
        validate_scores(changed, report)


def test_review_preserves_original_hybrid_seed_score_and_refuses_score_text_substitution():
    from dataclasses import asdict
    from uuid import UUID

    from enterprise_doc_core.documents.retrieval import RetrievalCandidate

    seed = RetrievalCandidate(
        chunk_id=UUID(int=1),
        tenant_id=UUID(int=2),
        document_version_id=UUID(int=3),
        generation_id=UUID(int=4),
        text="完整原文。",
        end_offset=5,
        score=0.023,
    )
    serialized = {k: str(v) if isinstance(v, UUID) else v for k, v in asdict(seed).items()}
    case = {
        "key": "q1",
        "query": "原文",
        "passages": [{"id": str(seed.chunk_id), "text": seed.text}],
        "legacy_ids": [str(seed.chunk_id)],
        "windows": [
            {"seeds": [serialized], "chunks": [{**serialized, "score": 0.0, "chunk_index": 0}]}
        ],
    }
    case["input_fingerprint"] = fingerprint({"query": case["query"], "passages": case["passages"]})
    plan = {"cases": [case]}
    report = {
        "plan_fingerprint": fingerprint(plan),
        "cases": {
            "q1": {
                "input_fingerprint": case["input_fingerprint"],
                "scores": [{"id": str(seed.chunk_id), "score": 0.5}],
            }
        },
    }
    result = review(plan, report)
    assert result["cases"][0]["selected"][0]["score"] == 0.023
    case["passages"][0]["text"] = "替换内容。"
    case["input_fingerprint"] = fingerprint({"query": case["query"], "passages": case["passages"]})
    report["cases"]["q1"]["input_fingerprint"] = case["input_fingerprint"]
    report["plan_fingerprint"] = fingerprint(plan)
    with pytest.raises(ValueError, match="authorized window text"):
        review(plan, report)
