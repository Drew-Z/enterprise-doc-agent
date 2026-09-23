"""Offline replay of immutable v4 provider responses; never dispatches a model."""

import hashlib
import json
import statistics
from pathlib import Path

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import CitationInput
from scripts.evaluate_presales_quality import Gold, load_dataset, write_json

ROOT = Path(__file__).resolve().parents[3]


def replay(name: str) -> dict:
    dataset_path = ROOT / "evaluation" / (name + ".json")
    dataset, digest = load_dataset(dataset_path)
    gold = Gold.model_validate_json(dataset_path.with_suffix(".gold.json").read_bytes())
    raw_path = dataset_path.with_suffix(".v4-gateway.json")
    raw = json.loads(raw_path.read_bytes())
    assert raw["status"] == "collected" and raw["schemaVersion"] == "presales-gateway-run-v1"
    assert digest == gold.dataset_sha256 == raw["datasetSha256"]
    assert len(raw["observations"]) == len(dataset.requirements) == len(gold.rows)
    sources = {raw["versions"][s.key]: s for s in dataset.sources}
    targets = {r.key: r for r in gold.rows}
    rows = []
    usage = []
    for observation in raw["observations"]:
        assert observation["providerRequests"] == 1 and len(observation["traces"]) == 1
        trace = observation["traces"][0]
        target = targets[observation["key"]]
        assert trace["input"]["requirement"] == next(
            r.model_dump(mode="json", by_alias=True)
            for r in dataset.requirements
            if r.key == target.key
        )
        catalog = {}
        for evidence in trace["input"]["evidence"]:
            assert evidence["text"] in sources[evidence["documentVersionId"]].content
            assert evidence["citationId"] not in catalog
            catalog[evidence["citationId"]] = CitationInput(
                chunk_id=evidence["chunkId"],
                document_version_id=evidence["documentVersionId"],
                excerpt=evidence["text"],
            )
        row = {
            "key": target.key,
            "initialState": observation["state"],
            "expectedStatus": target.status,
            "requiredEvidence": len(target.required_evidence),
        }
        try:
            assert trace["httpStatus"] == 200
            result = OpenAICompatiblePresalesGateway._decode(
                json.dumps(trace["response"], ensure_ascii=False).encode(), catalog
            )
            draft = result.draft
            covered = sum(
                any(
                    sources[str(c.document_version_id)].key == anchor.source_key
                    and anchor.excerpt in c.excerpt
                    for c in draft.citations
                )
                for anchor in target.required_evidence
            )
            row.update(
                result=result.model_dump(mode="json", by_alias=True),
                replayState="succeeded",
                statusMatch=draft.status == target.status,
                unsafeAffirmative=(draft.status == "supported" and target.status != "supported")
                or (
                    draft.status == "conditional"
                    and target.status not in {"supported", "conditional"}
                ),
                coveredRequiredEvidence=covered,
                citations=len(draft.citations),
            )
        except PresalesError as error:
            row.update(
                replayState="failed",
                errorCode=error.code,
                statusMatch=False,
                unsafeAffirmative=False,
                coveredRequiredEvidence=0,
                citations=0,
            )
        rows.append(row)
        usage.append(trace.get("response", {}).get("usage"))
    report = {
        "scope": "offline_decode_replay_of_generation_only_trial; no_new_provider_requests",
        "datasetSha256": digest,
        "originalRunSha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "promptSha256": sorted({o["provenance"]["promptSha256"] for o in raw["observations"]}),
        "decoderSourceSha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in [
                "packages/core/src/enterprise_doc_core/presales/citation_selection.py",
                "packages/core/src/enterprise_doc_core/presales/gateway.py",
            ]
        },
        "rows": rows,
        "expectedRows": len(targets),
        "initialSucceeded": sum(r["initialState"] == "succeeded" for r in rows),
        "replaySucceeded": sum(r["replayState"] == "succeeded" for r in rows),
        "statusMatches": sum(r["statusMatch"] for r in rows),
        "unsafeAffirmatives": sum(r["unsafeAffirmative"] for r in rows),
        "requiredEvidence": sum(r["requiredEvidence"] for r in rows),
        "coveredRequiredEvidence": sum(r["coveredRequiredEvidence"] for r in rows),
        "citations": sum(r["citations"] for r in rows),
        "realProviderRequests": sum(o["providerRequests"] for o in raw["observations"]),
        "replayProviderRequests": 0,
        "usage": {
            k: sum(u[k] for u in usage)
            if all(u and isinstance(u.get(k), int) for u in usage)
            else None
            for k in ["prompt_tokens", "completion_tokens", "total_tokens"]
        },
        "costAmount": None,
        "costReason": "Provider tick unit and invoice not independently verified",
        "latencySeconds": {
            "min": min(o["elapsedSeconds"] for o in raw["observations"]),
            "median": statistics.median(o["elapsedSeconds"] for o in raw["observations"]),
            "max": max(o["elapsedSeconds"] for o in raw["observations"]),
        },
        "semanticReviewRequired": True,
        "independentDomainReview": False,
    }
    write_json(dataset_path.with_suffix(".v4-gateway.replay.json"), report, exclusive=True)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"rows", "decoderSourceSha256"}},
            ensure_ascii=False,
        )
    )
    return report


if __name__ == "__main__":
    for dataset in ("presales_quality_holdout_v1", "presales_quality_holdout_v2"):
        replay(dataset)
