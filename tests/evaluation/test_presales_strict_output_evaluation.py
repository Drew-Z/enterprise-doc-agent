import copy
import hashlib
import json

import httpx
import pytest
from pydantic import SecretStr
from scripts.evaluate_presales_gateway import collect
from scripts.evaluate_presales_quality import write_json
from scripts.score_presales_gateway import score
from scripts.score_presales_prerequisites import score_prerequisites

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.presales.output_contract import strict_response_format
from enterprise_doc_core.presales.support_contract import constrained_response_format


@pytest.fixture
def inputs(tmp_path):
    dataset = tmp_path / "input.json"
    write_json(
        dataset,
        {
            "schemaVersion": "presales-quality-input-v1",
            "synthetic": True,
            "provenance": "Controlled HTTP contract fixture, not a model-quality evaluation",
            "title": "严格输出边界",
            "sources": [
                {
                    "key": "S1",
                    "filename": "scope.txt",
                    "applicability": "合成资料",
                    "content": "本页只列出服务名称\uff1a示例系统。",
                }
            ],
            "requirements": [{"key": "R1", "text": "说明数据驻留区域。"}],
        },
    )
    gold = tmp_path / "gold.json"
    write_json(
        gold,
        {
            "schemaVersion": "presales-quality-gold-v1",
            "datasetSha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "reviewStatus": "Controlled fixture, not independent review",
            "rows": [
                {
                    "key": "R1",
                    "status": "insufficient_evidence",
                    "requiredEvidence": [],
                    "referenceAnswer": "现有资料没有说明数据驻留区域。",
                    "reviewPoints": [],
                }
            ],
        },
    )
    return dataset, gold


@pytest.mark.parametrize("invalid", [False, True])
async def test_strict_collector_and_both_scorers_preserve_first_outcome(tmp_path, inputs, invalid):
    def respond(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        value = {
            "status": "insufficient_evidence",
            "answer": "现有资料没有说明数据驻留区域。",
            "missingInformation": ["请补充数据驻留区域说明。"],
            "citations": [],
            "prerequisites": [],
        }
        if invalid:
            value["missingInformation"] = "请补充数据驻留区域说明。"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]
            },
        )

    run_path = tmp_path / "run.json"
    report = await collect(
        inputs[0],
        run_path,
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        model_route="primary",
        strict_output=True,
        transport=httpx.MockTransport(respond),
    )
    assert report["schemaVersion"] == "presales-gateway-run-v10"
    observation = report["observations"][0]
    assert observation["provenance"]["promptVersion"] == "presales.v20"
    assert observation["providerRequests"] == 1
    if invalid:
        assert observation["state"] == "failed" and observation["errorDiagnostic"] == "draft_schema"
    result = score(*inputs, report)
    assert result["acceptedDrafts"] == (0 if invalid else 1)
    expected = tmp_path / "expected.json"
    write_json(
        expected,
        {
            "schemaVersion": "presales-prerequisite-gold-v1",
            "datasetSha256": report["datasetSha256"],
            "reviewStatus": "Controlled fixture",
            "rows": [{"key": "R1", "prerequisites": []}],
        },
    )
    review = tmp_path / "review.json"
    write_json(
        review,
        {
            "schemaVersion": "presales-prerequisite-review-v1",
            "runSha256": hashlib.sha256(run_path.read_bytes()).hexdigest(),
            "expectationsSha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
            "reviewer": "test",
            "reviewType": "assistant",
            "rows": [{"key": "R1", "reviewed": not invalid, "mappings": []}],
        },
    )
    scored = score_prerequisites(*inputs, run_path, expected, review)
    assert scored["prerequisiteCheckPassed"] is not invalid

    for version, prompt, format_fn in [
        ("v8", "presales.v18", strict_response_format),
        ("v9", "presales.v19", constrained_response_format),
    ]:
        historical = copy.deepcopy(report)
        historical["schemaVersion"] = "presales-gateway-run-" + version
        historical["observations"][0]["provenance"]["promptVersion"] = prompt
        trace = historical["observations"][0]["traces"][0]
        trace["responseFormat"] = format_fn()
        trace["input"].pop("spans")
        assert score(*inputs, historical) == result
        historical_path = tmp_path / f"historical-{version}.json"
        write_json(historical_path, historical)
        historical_review = json.loads(review.read_bytes())
        historical_review["runSha256"] = hashlib.sha256(historical_path.read_bytes()).hexdigest()
        historical_review_path = tmp_path / f"historical-review-{version}.json"
        write_json(historical_review_path, historical_review)
        assert (
            score_prerequisites(*inputs, historical_path, expected, historical_review_path)[
                "prerequisiteCheckPassed"
            ]
            is not invalid
        )
        historical["schemaVersion"] = "presales-gateway-run-v10"
        with pytest.raises(ValueError, match="strict_output_contract_mismatch"):
            score(*inputs, historical)

    changed = copy.deepcopy(report)
    changed["observations"][0]["traces"][0]["input"]["spans"][0]["text"] = "伪造原文。"
    with pytest.raises(ValueError, match="wire_span_projection_mismatch"):
        score(*inputs, changed)

    changed = copy.deepcopy(report)
    changed["observations"][0]["traces"][0]["responseFormat"] = {"type": "json_object"}
    with pytest.raises(ValueError, match="strict_output_contract_mismatch"):
        score(*inputs, changed)
    if not invalid:
        changed = copy.deepcopy(report)
        message = changed["observations"][0]["traces"][0]["response"]["choices"][0]["message"]
        value = json.loads(message["content"])
        value.pop("citations")
        message["content"] = json.dumps(value)
        with pytest.raises(ValueError):
            score(*inputs, changed)
        # Omitted optional arrays remain accepted only by the historical v6 contract.
        changed["schemaVersion"] = "presales-gateway-run-v6"
        changed["observations"][0]["traces"][0]["input"].pop("spans")
        assert score(*inputs, changed)["acceptedDrafts"] == 1


async def test_span_evidence_and_prerequisites_are_bound_through_both_scorers(tmp_path, inputs):
    dataset = json.loads(inputs[0].read_bytes())
    source = "启用前必须配置驻留区域。驻留区域已配置为境内。"
    dataset["sources"][0]["content"] = source
    write_json(inputs[0], dataset)
    digest = hashlib.sha256(inputs[0].read_bytes()).hexdigest()
    gold = json.loads(inputs[1].read_bytes())
    gold["datasetSha256"] = digest
    anchor = {"sourceKey": "S1", "excerpt": source}
    gold["rows"][0].update(status="supported", requiredEvidence=[anchor])
    write_json(inputs[1], gold)

    def respond(request):
        wire = json.loads(json.loads(request.content)["messages"][1]["content"])
        refs = {span["text"]: {"spanId": span["spanId"]} for span in wire["spans"]}
        value = {
            "status": "supported",
            "answer": "驻留区域已配置为境内。",
            "missingInformation": [],
            "citations": [],
            "prerequisites": [
                {
                    "proposition": "驻留区域已配置",
                    "definition": [refs["启用前必须配置驻留区域。"]],
                    "positive": [refs["驻留区域已配置为境内。"]],
                    "negative": [],
                    "unconfirmed": [],
                    "uncertainty": "none",
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]
            },
        )

    run_path = tmp_path / "span-run.json"
    report = await collect(
        inputs[0],
        run_path,
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled",
        ),
        model_route="primary",
        strict_output=True,
        transport=httpx.MockTransport(respond),
    )
    baseline = score(*inputs, report)
    assert baseline["acceptedDrafts"] == baseline["coveredRequiredEvidence"] == 1
    expected = tmp_path / "span-expected.json"
    write_json(
        expected,
        {
            "schemaVersion": "presales-prerequisite-gold-v1",
            "datasetSha256": digest,
            "reviewStatus": "Controlled fixture",
            "rows": [
                {
                    "key": "R1",
                    "prerequisites": [
                        {
                            "key": "REGION",
                            "description": "驻留区域已配置",
                            "state": "met",
                            "requiredEvidence": [anchor],
                        }
                    ],
                }
            ],
        },
    )
    review = tmp_path / "span-review.json"
    write_json(
        review,
        {
            "schemaVersion": "presales-prerequisite-review-v1",
            "runSha256": hashlib.sha256(run_path.read_bytes()).hexdigest(),
            "expectationsSha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
            "reviewer": "test",
            "reviewType": "assistant",
            "rows": [
                {
                    "key": "R1",
                    "reviewed": True,
                    "mappings": [
                        {
                            "expectedKey": "REGION",
                            "observedIndex": 0,
                            "reason": "Controlled region prerequisite",
                        }
                    ],
                }
            ],
        },
    )
    assert score_prerequisites(*inputs, run_path, expected, review)["prerequisiteCheckPassed"]

    for fault in ("spanId", "citationId", "remove", "duplicate", "order"):
        changed = copy.deepcopy(report)
        spans = changed["observations"][0]["traces"][0]["input"]["spans"]
        if fault in {"spanId", "citationId"}:
            spans[0][fault] = "foreign"
        elif fault == "remove":
            spans.pop()
        elif fault == "duplicate":
            spans.append(spans[0])
        else:
            spans.reverse()
        with pytest.raises(ValueError, match="wire_span_projection_mismatch"):
            score(*inputs, changed)
