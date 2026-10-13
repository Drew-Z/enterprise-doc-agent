"""Offline Haystack qualification on public known regressions; no model or DB calls.

Run in an isolated Python 3.12 environment with haystack-ai==3.3.0 and psutil==7.0.0.
Historical cited chunks are controlled seeds, not reconstructed live retrieval ranks.
Merged windows are diagnostics, NOT production citation objects.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

SCOPE = ["tenant_id", "document_version_id", "generation_id", "source_id"]
EXPECTED = dict(zip(SCOPE, ["tenant-a", "version-a", "generation-a", "source-a"], strict=True))


def deny_network(event, _args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("Network disabled in offline context qualification")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_documents(documents, source):
    for doc in documents:
        if any(doc.meta.get(key) != value for key, value in EXPECTED.items()):
            raise ValueError("foreign source scope")
        start = doc.meta["split_idx_start"]
        if doc.content != source[start : start + len(doc.content)]:
            raise ValueError("content is not an original source slice")


def exact_windows(seeds, documents, windows, radius, source):
    validate_documents(seeds, source)
    validate_documents(documents, source)
    records = []
    for seed, text in zip(seeds, windows, strict=True):
        members = {
            doc.id: doc
            for doc in documents
            if abs(doc.meta["split_id"] - seed.meta["split_id"]) <= radius
        }
        start = min(doc.meta["split_idx_start"] for doc in members.values())
        end = max(doc.meta["split_idx_start"] + len(doc.content) for doc in members.values())
        if text != source[start:end]:
            raise ValueError("merged window is not one contiguous original slice")
        records.append(
            {"start": start, "end": end, "chars": len(text), "sha256": digest(text.encode())}
        )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"
    os.environ["HAYSTACK_AUTO_TRACE_ENABLED"] = "false"
    sys.addaudithook(deny_network)
    started = time.perf_counter()
    import psutil
    from haystack import Document
    from haystack.components.retrievers import SentenceWindowRetriever
    from haystack.document_stores.in_memory import InMemoryDocumentStore

    assert importlib.metadata.version("haystack-ai") == "3.3.0"
    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    results = []
    checks = []
    for case in fixture["cases"]:
        source = case["source_text"]
        assert digest(source.encode()) == case["source_sha256"]
        documents = [
            Document(
                id=f"original-{index}",
                content=source[offset : offset + 1200],
                meta={**EXPECTED, "split_id": index, "split_idx_start": offset},
            )
            for index, offset in enumerate(range(0, len(source), 1080))
        ]
        seeds = [documents[offset // 1080] for offset in case["historical_cited_offsets"]]
        validate_documents(seeds, source)
        anchor = case["required_excerpt"]
        # Metadata collision controls are deliberately synthetic and never provider input.
        foreign = [
            Document(
                id=f"foreign-{key}",
                content=f"FOREIGN {key}",
                meta={**seeds[0].meta, key: "foreign"},
            )
            for key in SCOPE[:3]
        ]
        store = InMemoryDocumentStore()
        try:
            store.write_documents(documents + foreign)
            unsafe = SentenceWindowRetriever(store, window_size=1).run(seeds)
            unsafe_foreign = [
                doc.id for doc in unsafe["context_documents"] if doc.id.startswith("foreign-")
            ]
            assert set(unsafe_foreign) == {doc.id for doc in foreign}
            checks.append(
                {
                    "case": case["original_id"],
                    "default_source_id_only_admits_foreign_controls": True,
                }
            )
            retriever = SentenceWindowRetriever(store, source_id_meta_field=SCOPE)
            arms = []
            for radius in (1, 3):
                tick = time.perf_counter()
                result = retriever.run(seeds, window_size=radius)
                elapsed_ms = (time.perf_counter() - tick) * 1000
                texts = result["context_windows"]
                records = exact_windows(seeds, result["context_documents"], texts, radius, source)
                # Diagnose the existing prefix cap; this is not proposed silent truncation.
                caps = []
                for cap in (1200, 1800):
                    caps.append(
                        {
                            "chars_per_window": cap,
                            "actual_chars": sum(len(text[:cap]) for text in texts),
                            "required_anchor_present": any(anchor in text[:cap] for text in texts),
                            "all_original_seed_text_retained": all(
                                seed.content in text[:cap]
                                for seed, text in zip(seeds, texts, strict=True)
                            ),
                        }
                    )
                arms.append(
                    {
                        "radius": radius,
                        "component_ms": elapsed_ms,
                        "raw_windows": records,
                        "raw_total_chars": sum(map(len, texts)),
                        "raw_required_anchor_present": any(anchor in text for text in texts),
                        "budget_diagnostics": caps,
                        "scope_and_literal_validation": "pass",
                    }
                )
            # Missing provenance must fail, rather than silently broaden context.
            missing = Document(
                content=seeds[0].content,
                meta={k: v for k, v in seeds[0].meta.items() if k != "generation_id"},
            )
            try:
                retriever.run([missing])
            except ValueError:
                checks.append({"case": case["original_id"], "missing_generation_rejected": True})
            else:
                raise AssertionError("missing provenance accepted")
            try:
                validate_documents([foreign[0]], source)
            except ValueError:
                checks.append({"case": case["original_id"], "foreign_seed_rejected": True})
            else:
                raise AssertionError("foreign seed accepted")
            gap_docs = [documents[0], documents[2]]
            try:
                exact_windows(
                    [documents[0]],
                    gap_docs,
                    [SentenceWindowRetriever.merge_documents_text(gap_docs)],
                    3,
                    source,
                )
            except ValueError:
                checks.append({"case": case["original_id"], "noncontiguous_merge_rejected": True})
            else:
                raise AssertionError("noncontiguous citation accepted")
            results.append(
                {
                    "case": case["original_id"],
                    "source_sha256": case["source_sha256"],
                    "anchor_offset": source.index(anchor),
                    "historical_seed_offsets": case["historical_cited_offsets"],
                    "baseline_chars": sum(len(doc.content) for doc in seeds),
                    "baseline_required_anchor_present": any(anchor in doc.content for doc in seeds),
                    "arms": arms,
                }
            )
        finally:
            store.shutdown()
    info = psutil.Process().memory_info()
    report = {
        "format": "haystack-context-offline-v1",
        "fixture_sha256": digest(fixture_bytes),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "python": sys.version,
        "elapsed_s_including_imports": time.perf_counter() - started,
        "rss_bytes": info.rss,
        "peak_working_set_bytes": getattr(info, "peak_wset", None),
        "network_disabled": True,
        "generation_calls": 0,
        "embedding_calls": 0,
        "live_retrieval": False,
        "model_semantic_evaluation": False,
        "cases": results,
        "boundary_checks": checks,
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "cases": len(results),
                "boundary_checks": len(checks),
                "peak_working_set_bytes": report["peak_working_set_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
