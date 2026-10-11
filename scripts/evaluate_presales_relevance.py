"""Prepare/review offline relevance scores against immutable authorized chunk snapshots.

Preparation runs real Haystack windows. Ranking runs separately in its pinned environment;
review never loads weights or sends requests. This is not a deployed retrieval evaluation.
"""

import argparse
import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path
from uuid import UUID


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def candidate(value):
    from enterprise_doc_core.documents.retrieval import RetrievalCandidate

    data = {
        field.name: value[field.name] for field in fields(RetrievalCandidate) if field.name in value
    }
    for key in ("chunk_id", "tenant_id", "document_version_id", "generation_id"):
        data[key] = UUID(data[key])
    return RetrievalCandidate(**data)


def windows(case):
    from enterprise_doc_core.presales.context_window import WindowChunk

    return tuple(
        (
            tuple(candidate(c) for c in group["seeds"]),
            tuple(WindowChunk(candidate(c), c["chunk_index"]) for c in group["chunks"]),
        )
        for group in case["windows"]
    )


def prepare(corpus, bound):
    from enterprise_doc_core.presales.context_window import WINDOW_RADIUS, context_candidates

    chunks = corpus["snapshot"]["chunks"]
    by_id = {c["chunk_id"]: c for c in chunks}
    cases = []
    for key, fact in bound["facts"].items():
        if not fact.get("v25_candidates"):
            continue
        seeds = fact["v25_candidates"]
        groups = []
        for source in bound["inputs"][key]["v25"]["sources"]:
            version_seeds = [s for s in seeds if s["document_version_id"] == source["versionId"]]
            rows = [
                c
                for c in chunks
                if c["document_version_id"] == source["versionId"]
                and any(
                    abs(c["chunk_index"] - by_id[s["chunk_id"]]["chunk_index"]) <= WINDOW_RADIUS
                    for s in version_seeds
                )
            ]
            groups.append({"seeds": version_seeds, "chunks": rows})
        case = {
            "key": key,
            "query": bound["inputs"][key]["v25"]["requirement"]["text"],
            "windows": groups,
            "legacy_ids": fact["v26_chunk_ids"],
        }
        case["passages"] = [
            {"id": str(c.chunk_id), "text": c.text}
            for seeds_in_source, pool in windows(case)
            for c in context_candidates(seeds_in_source, pool)
        ]
        case["input_fingerprint"] = fingerprint(
            {"query": case["query"], "passages": case["passages"]}
        )
        cases.append(case)
    return {"format": "presales-relevance-plan-v1", "cases": cases}


def validate_scores(plan, report):
    if report["plan_fingerprint"] != fingerprint(plan):
        raise ValueError("Scores do not match the frozen plan")
    expected = {case["key"] for case in plan["cases"]}
    if set(report["cases"]) != expected:
        raise ValueError("Scores must cover exactly the planned cases")
    for case in plan["cases"]:
        actual = report["cases"][case["key"]]
        if (
            fingerprint({"query": case["query"], "passages": case["passages"]})
            != case["input_fingerprint"]
        ):
            raise ValueError("Plan query or passages changed after binding")
        if actual["input_fingerprint"] != case["input_fingerprint"]:
            raise ValueError("Ranker input differs from the offered query and passages")
        scores = actual["scores"]
        if len({s["id"] for s in scores}) != len(scores):
            raise ValueError("Duplicate score IDs")


def review(plan, report):
    from enterprise_doc_core.presales.context_window import (
        context_candidates,
        select_context,
        select_ranked_context,
    )

    validate_scores(plan, report)
    results = []
    for case in plan["cases"]:
        groups = windows(case)
        expected_passages = [
            {"id": str(c.chunk_id), "text": c.text}
            for seeds, pool in groups
            for c in context_candidates(seeds, pool)
        ]
        if expected_passages != case["passages"]:
            raise ValueError("Scored passages differ from authorized window text")
        slots = 12 - sum(len(seeds) for seeds, _ in groups)
        legacy = []
        for seeds, pool in groups:
            selected = select_context(seeds, pool, extra_slots=slots)
            legacy.extend(selected.candidates)
            slots -= len(selected.candidates) - len(seeds)
        if [str(c.chunk_id) for c in legacy] != case["legacy_ids"]:
            raise ValueError("Legacy selector no longer reproduces the frozen v26 input")
        selected = select_ranked_context(
            groups,
            {UUID(score["id"]): score["score"] for score in report["cases"][case["key"]]["scores"]},
        )
        # A seed also carries its original hybrid score; stored neighbors have no
        # retrieval score. Preserve the complete seed object, not the DB placeholder.
        originals = {
            c.chunk_id: c for seeds, pool in groups for c in [*(p.candidate for p in pool), *seeds]
        }
        chosen = [c for source in selected for c in source.candidates]
        assert all(c == originals[c.chunk_id] for c in chosen)
        seed_ids = {str(c.chunk_id) for seeds, _ in groups for c in seeds}
        assert seed_ids <= {str(c.chunk_id) for c in chosen}
        indexes = {
            c["chunk_id"]: c["chunk_index"] for group in case["windows"] for c in group["chunks"]
        }
        results.append(
            {
                "key": case["key"],
                "legacy_ids": case["legacy_ids"],
                "selected": [
                    {**asdict(c), "chunk_index": indexes[str(c.chunk_id)]} for c in chosen
                ],
                "extra_ids": [str(c.chunk_id) for c in chosen if str(c.chunk_id) not in seed_ids],
                "source_characters": [sum(len(c.text) for c in s.candidates) for s in selected],
                "all_seeds_and_originals_preserved": True,
                "global_chunk_count": len(chosen),
            }
        )
    return {
        "format": "presales-relevance-review-v1",
        "plan_fingerprint": fingerprint(plan),
        "scores_fingerprint": fingerprint(report),
        "cases": results,
        "generation_calls": 0,
        "embedding_calls": 0,
        "live_retrieval": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("prepare")
    create.add_argument("--corpus", type=Path, required=True)
    create.add_argument("--bound-inputs", type=Path, required=True)
    inspect = sub.add_parser("review")
    inspect.add_argument("--plan", type=Path, required=True)
    inspect.add_argument("--scores", type=Path, required=True)
    for command in (create, inspect):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.command == "prepare":
        value = prepare(
            json.loads(args.corpus.read_bytes()), json.loads(args.bound_inputs.read_bytes())
        )
        value["source_sha256"] = {
            str(p): digest(p.read_bytes()) for p in (args.corpus, args.bound_inputs)
        }
    else:
        value = review(json.loads(args.plan.read_bytes()), json.loads(args.scores.read_bytes()))
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, default=str)
        output.write("\n")
    print(json.dumps({"cases": len(value["cases"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
