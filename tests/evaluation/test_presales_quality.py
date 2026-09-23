from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from scripts.evaluate_presales_quality import collect, load_dataset, score

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "evaluation/presales_quality_v1.json"
GOLD = ROOT / "evaluation/presales_quality_v1.gold.json"


def packet_fixture():
    dataset, digest = load_dataset(INPUT)
    versions = {source.key: str(uuid4()) for source in dataset.sources}
    sources = [
        {
            "versionId": versions[source.key],
            "documentId": str(uuid4()),
            "generationId": str(uuid4()),
            "filename": source.filename,
            "versionNumber": 1,
            "latestVersionNumber": 1,
            "contentSha256": hashlib.sha256(source.content.encode()).hexdigest(),
            "applicability": source.applicability,
        }
        for source in dataset.sources
    ]
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    rows = []
    for requirement, expected in zip(dataset.requirements, gold["rows"], strict=True):
        rows.append(
            {
                "id": str(uuid4()),
                "requirement": requirement.model_dump(by_alias=True),
                "revision": 1,
                "state": "drafted",
                "review": None,
                "reviewHistory": [],
                "draft": {
                    "status": expected["status"],
                    "answer": expected["referenceAnswer"],
                    "conditions": ["增购两个选项并完成两项验收"]
                    if expected["status"] == "conditional"
                    else [],
                    "missingInformation": ["RTO 保证"]
                    if expected["status"] == "insufficient_evidence"
                    else [],
                    "retrieval": [],
                    "citations": [
                        {
                            "chunkId": str(uuid4()),
                            "documentVersionId": versions[e["sourceKey"]],
                            "excerpt": e["excerpt"],
                            "filename": next(
                                s.filename for s in dataset.sources if s.key == e["sourceKey"]
                            ),
                            "pageNumber": None,
                            "heading": None,
                            "startOffset": 0,
                            "endOffset": 500,
                        }
                        for e in expected["requiredEvidence"]
                    ],
                },
                "attempts": [
                    {
                        "id": str(uuid4()),
                        "number": 1,
                        "state": "succeeded",
                        "errorCode": None,
                        "modelProvider": "fixture",
                        "modelName": "fixture",
                        "providerRequestCount": 1,
                        "provenance": {},
                        "usage": None,
                        "createdAt": "2026-09-23T00:00:00Z",
                        "finishedAt": "2026-09-23T00:00:01Z",
                        "deadlineAt": "2026-09-23T00:02:30Z",
                    }
                ],
            }
        )
    packet = {
        "id": str(uuid4()),
        "title": dataset.title,
        "createdAt": "2026-09-23T00:00:00Z",
        "rowCount": len(rows),
        "staleSources": False,
        "sources": sources,
        "rows": rows,
    }
    report = {
        "schemaVersion": "presales-quality-run-v1",
        "datasetSha256": digest,
        "plannedRepeats": 1,
        "runs": [
            {
                "versions": versions,
                "packet": packet,
                "observations": [
                    {"key": r.key, "elapsedSeconds": 1.0} for r in dataset.requirements
                ],
            }
        ],
    }
    return dataset, packet, report


def test_score_counts_missing_attempts_and_unknown_cost_without_inventing_zero():
    _, _, report = packet_fixture()
    report["plannedRepeats"] = 2
    result = score(INPUT, GOLD, report)
    assert result["expectedRows"] == 12
    assert result["statusMatches"] == 6
    assert result["missingDrafts"] == 6
    assert result["validCitations"] == result["citations"] == 12
    assert result["usage"]["prompt_tokens"] is None
    assert result["costAmount"] is None
    assert result["latencySeconds"]["sampleCount"] == 6


def test_score_detects_false_commitment_fabricated_quote_and_missing_conflict_side():
    _, packet, report = packet_fixture()
    packet["rows"][2]["draft"]["status"] = "supported"
    packet["rows"][0]["draft"]["citations"][0]["excerpt"] = "虚构的承诺"
    packet["rows"][5]["draft"]["citations"].pop()
    result = score(INPUT, GOLD, report)
    assert result["unsafeAffirmatives"] == 1
    assert result["validCitations"] < result["citations"]
    assert result["coveredRequiredEvidence"] < result["requiredEvidence"]


def test_score_rejects_hash_and_source_binding_changes():
    _, packet, report = packet_fixture()
    altered = copy.deepcopy(report)
    altered["datasetSha256"] = "0" * 64
    with pytest.raises(ValueError, match="dataset_hash_mismatch"):
        score(INPUT, GOLD, altered)
    packet["sources"][0]["contentSha256"] = "0" * 64
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        score(INPUT, GOLD, report)


def test_collect_uses_fresh_cookie_separates_object_upload_and_never_retries(tmp_path):
    dataset, packet, _ = packet_fixture()
    generated = []
    uploaded = []
    logout = []
    calls = []
    by_filename = {source["filename"]: source["versionId"] for source in packet["sources"]}

    def control(request):
        calls.append(request)
        path = request.url.path
        if path == "/auth/demo":
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                headers={"set-cookie": "session=secret-cookie; Secure; Path=/"},
                json={
                    "demo": True,
                    "contextVersion": "c" * 32 + ".1",
                    "csrfToken": "d" * 64,
                    "currentTenant": {"tenantId": str(uuid4())},
                    "expiresAt": "2026-09-23T02:00:00Z",
                },
            )
        assert request.headers["cookie"] == "session=secret-cookie"
        assert request.headers["x-session-context"] == "c" * 32 + ".1"
        if request.method != "GET":
            assert request.headers["x-csrf-token"] == "d" * 64
        if path == "/api/upload-sessions":
            return httpx.Response(
                201, json={"sessionId": by_filename[json.loads(request.content)["filename"]]}
            )
        if path.endswith("/presign"):
            return httpx.Response(
                200,
                json={
                    "url": "https://objects.test/upload?signature=private",
                    "headers": {"x-amz-checksum-sha256": "checksum"},
                },
            )
        if path.endswith("/complete"):
            return httpx.Response(200, json={"versionId": path.split("/")[3]})
        if path == "/api/documents":
            return httpx.Response(
                200, json=[{"versionId": v, "versionStatus": "ready"} for v in by_filename.values()]
            )
        if path == "/api/presales" and request.method == "POST":
            body = json.loads(request.content)
            assert "referenceAnswer" not in request.content.decode()
            assert body["requirements"] == [
                r.model_dump(by_alias=True) for r in dataset.requirements
            ]
            return httpx.Response(201, json=packet)
        if path.endswith("/generate"):
            generated.append(path)
            if len(generated) == 1:
                raise httpx.ReadTimeout("uncertain response with secret", request=request)
            return httpx.Response(503, json={"error": {"code": "presales_model_timeout"}})
        if path == "/api/demo":
            return httpx.Response(200, json={"attemptsUsed": len(generated), "attemptLimit": 6})
        if path == "/auth/logout":
            logout.append(True)
            return httpx.Response(200, json={"signedOut": True})
        if path == f"/api/presales/{packet['id']}":
            return httpx.Response(200, json=packet)
        raise AssertionError(path)

    def objects(request):
        assert not (
            {"cookie", "authorization", "x-csrf-token", "x-session-context"} & set(request.headers)
        )
        uploaded.append(request.content)
        return httpx.Response(200, headers={"etag": "test-etag"})

    output = tmp_path / "run.json"
    collect(
        INPUT,
        output,
        base_url="https://app.test",
        object_hosts=("objects.test",),
        repeats=1,
        transport=httpx.MockTransport(control),
        object_transport=httpx.MockTransport(objects),
    )
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert len(generated) == len(set(generated)) == 6
    assert len(uploaded) == 6 and logout == [True]
    assert saved["runs"][0]["observations"][0]["transportError"] == "ReadTimeout"
    assert "secret-cookie" not in output.read_text()
    assert "private" not in output.read_text()
    assert "csrfToken" not in output.read_text()
    with pytest.raises(FileExistsError):
        collect(
            INPUT, output, base_url="https://app.test", object_hosts=("objects.test",), repeats=1
        )
