from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from enterprise_doc_core.documents.evaluation import (
    RetrievalEvalCase,
    evaluate_retrieval_cases,
)


def _case(raw: dict[str, Any]) -> RetrievalEvalCase:
    return RetrievalEvalCase(
        case_id=str(raw["case_id"]),
        relevant_chunk_ids=tuple(map(str, raw["relevant_chunk_ids"])),
        retrieved_chunk_ids=tuple(map(str, raw["retrieved_chunk_ids"])),
        expected_refusal=bool(raw["expected_refusal"]),
        predicted_refusal=bool(raw["predicted_refusal"]),
        golden_citation_ids=tuple(map(str, raw.get("golden_citation_ids", ()))),
        predicted_citation_ids=tuple(map(str, raw.get("predicted_citation_ids", ()))),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=Path("evaluation/m3_retrieval_eval_v1.json"),
    )
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate_retrieval_cases(
        tuple(_case(raw) for raw in payload["cases"]),
        k=int(payload["k"]),
    )
    print(
        json.dumps(
            {
                "dataset_version": payload["version"],
                "limitations": payload["limitations"],
                **asdict(report),
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
