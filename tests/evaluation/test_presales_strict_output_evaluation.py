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
    assert report["schemaVersion"] == "presales-gateway-run-v8"
    observation = report["observations"][0]
    assert observation["provenance"]["promptVersion"] == "presales.v18"
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
        assert score(*inputs, changed)["acceptedDrafts"] == 1
