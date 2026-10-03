"""Offline scoring of original generation-only outcomes, including failed attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

from enterprise_doc_core.presales.citation_selection import (
    SelectionInput,
    prepare_citations,
    resolve_selection,
)
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import CitationInput, GeneratedDraft, GenerationInput
from scripts.evaluate_presales_gateway import synthetic_sources
from scripts.evaluate_presales_quality import Gold, load_dataset, write_json


def bind_projected_input(
    wire: dict[str, Any], source_input: GenerationInput
) -> dict[str, CitationInput]:
    """Verify the recorded wire projection, without decoding any failed response."""
    offered = SelectionInput.model_validate(wire)
    expected, identities = prepare_citations(source_input)
    if offered.requirement != expected.requirement or len(offered.evidence) != len(
        expected.evidence
    ):
        raise ValueError("wire_projection_mismatch")
    catalog: dict[str, CitationInput] = {}
    prefix = offered.evidence[0].citation_id.rsplit("_", 1)[0] if offered.evidence else ""
    if offered.evidence and not re.fullmatch(r"cite_[0-9a-f]{12}", prefix):
        raise ValueError("wire_reference_mismatch")
    for index, (actual, wanted, identity) in enumerate(
        zip(offered.evidence, expected.evidence, identities.values(), strict=True), start=1
    ):
        if actual.citation_id != f"{prefix}_{index}" or actual.model_dump(
            exclude={"citation_id"}
        ) != wanted.model_dump(exclude={"citation_id"}):
            raise ValueError("wire_projection_mismatch")
        catalog[actual.citation_id] = identity
    return catalog


def score(dataset_path: Path, gold_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    dataset, digest = load_dataset(dataset_path)
    gold = Gold.model_validate_json(gold_path.read_bytes())
    if digest != gold.dataset_sha256 or digest != report["datasetSha256"]:
        raise ValueError("dataset_hash_mismatch")
    if report["schemaVersion"] not in {
        "presales-gateway-run-v1",
        "presales-gateway-run-v2",
        "presales-gateway-run-v3",
    }:
        raise ValueError("invalid_report_scope")
    projected = report["schemaVersion"] != "presales-gateway-run-v1"
    structured = report["schemaVersion"] == "presales-gateway-run-v3"
    requirements = {r.key: r for r in dataset.requirements}
    sources = {s.key: s for s in dataset.sources}
    expected = {r.key: r for r in gold.rows}
    if len(expected) != len(gold.rows) or set(expected) != set(requirements):
        raise ValueError("gold_coverage_mismatch")
    for target in gold.rows:
        if any(
            e.source_key not in sources or e.excerpt not in sources[e.source_key].content
            for e in target.required_evidence
        ):
            raise ValueError("invalid_gold_anchor")
    versions = report["versions"]
    reverse = {value: key for key, value in versions.items()}
    if set(versions) != set(sources) or len(reverse) != len(sources):
        raise ValueError("source_binding_mismatch")
    snapshots, complete_evidence = synthetic_sources(dataset, digest)
    if projected and versions != {
        source.key: str(snapshot.version_id)
        for source, snapshot in zip(dataset.sources, snapshots, strict=True)
    }:
        raise ValueError("source_binding_mismatch")
    observations = {row["key"]: row for row in report["observations"]}
    if (
        len(observations) != len(report["observations"])
        or not set(observations) <= requirements.keys()
    ):
        raise ValueError("unexpected_or_duplicate_row")
    rows: list[dict[str, Any]] = []
    usages, latencies = [], []
    requests = 0
    for key, target in expected.items():
        observation = observations.get(key, {})
        traces = observation.get("traces", [])
        if len(traces) > 1 or observation.get("providerRequests", 0) != len(traces):
            raise ValueError("unexpected_request_count")
        requests += len(traces)
        if observation.get("elapsedSeconds") is not None:
            latencies.append(observation["elapsedSeconds"])
        evidence = []
        catalog = {}
        for trace in traces:
            payload = GenerationInput.model_validate(
                observation["sourceInput"] if projected else trace["input"]
            )
            if projected:
                expected_input = GenerationInput(
                    requirement=requirements[key], sources=snapshots, evidence=complete_evidence
                )
                if payload != expected_input:
                    raise ValueError("source_binding_mismatch")
                catalog = bind_projected_input(trace["input"], payload)
            if payload.requirement != requirements[key]:
                raise ValueError("requirement_mismatch")
            if {str(s.version_id) for s in payload.sources} != set(reverse):
                raise ValueError("source_binding_mismatch")
            for snapshot in payload.sources:
                source = sources[reverse[str(snapshot.version_id)]]
                if snapshot.content_sha256 != hashlib.sha256(source.content.encode()).hexdigest():
                    raise ValueError("source_hash_mismatch")
                if snapshot.applicability != source.applicability:
                    raise ValueError("source_scope_mismatch")
            evidence = payload.evidence
            if projected:
                evidence = [
                    {
                        "chunkId": str(c.chunk_id),
                        "documentVersionId": str(c.document_version_id),
                        "text": c.excerpt,
                    }
                    for c in catalog.values()
                ]
            if any(
                item["documentVersionId"] not in reverse
                or item["text"] not in sources[reverse[item["documentVersionId"]]].content
                for item in evidence
            ):
                raise ValueError("source_text_mismatch")
            usages.append(trace.get("response", {}).get("usage"))
        draft = None
        if observation.get("state") == "succeeded":
            if len(traces) != 1 or traces[0]["httpStatus"] != 200:
                raise ValueError("success_without_response")
            draft = GeneratedDraft.model_validate(observation["result"]).draft
            if structured != (draft.prerequisites is not None):
                raise ValueError("result_prerequisites_contract_mismatch")
            if projected:
                try:
                    original = trace["response"]["choices"][0]["message"]["content"]
                    resolved = resolve_selection(original, catalog)
                    # v2 saved only the flat projection. Reproduce that contract without
                    # rewriting the historical result or accepting a formerly failed call.
                    if not structured:
                        resolved = resolved.model_copy(update={"prerequisites": None})
                    if resolved != draft:
                        raise ValueError("result_binding_mismatch")
                except (KeyError, IndexError, TypeError, PresalesError) as error:
                    raise ValueError("result_binding_mismatch") from error
        valid = (
            [
                citation
                for citation in draft.citations
                if any(
                    str(citation.chunk_id) == e["chunkId"]
                    and str(citation.document_version_id) == e["documentVersionId"]
                    and citation.excerpt == e["text"]
                    for e in evidence
                )
            ]
            if draft
            else []
        )
        covered = sum(
            any(
                reverse[str(c.document_version_id)] == anchor.source_key
                and anchor.excerpt in c.excerpt
                for c in valid
            )
            for anchor in target.required_evidence
        )
        predicted = draft.status if draft else None
        rows.append(
            {
                "key": key,
                "expectedStatus": target.status,
                "predictedStatus": predicted,
                "acceptedDraft": draft is not None,
                "statusMatch": predicted == target.status,
                "unsafeAffirmative": (predicted == "supported" and target.status != "supported")
                or (
                    predicted == "conditional" and target.status not in {"supported", "conditional"}
                ),
                "citations": len(draft.citations) if draft else 0,
                "validCitations": len(valid),
                "requiredEvidence": len(target.required_evidence),
                "coveredRequiredEvidence": covered,
            }
        )
    result = {
        "schemaVersion": "presales-gateway-score-v1",
        "scope": "original_generation_only_outcomes",
        "datasetSha256": digest,
        "expectedRows": len(expected),
        "rows": rows,
        "realProviderRequests": requests,
        "semanticReviewRequired": True,
        "independentDomainReview": False,
        "usage": {
            k: sum(u[k] for u in usages)
            if usages and all(u and type(u.get(k)) is int for u in usages)
            else None
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "costAmount": None,
        "costReason": "No verified unit price or invoice",
        "latencySeconds": {
            "min": min(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }
    for name, field in (
        ("acceptedDrafts", "acceptedDraft"),
        ("statusMatches", "statusMatch"),
        ("unsafeAffirmatives", "unsafeAffirmative"),
        ("citations", "citations"),
        ("validCitations", "validCitations"),
        ("requiredEvidence", "requiredEvidence"),
        ("coveredRequiredEvidence", "coveredRequiredEvidence"),
    ):
        result[name] = sum(row[field] for row in rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.input, args.gold, json.loads(args.run.read_bytes()))
    result["originalRunSha256"] = hashlib.sha256(args.run.read_bytes()).hexdigest()
    write_json(args.output, result, exclusive=True)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
