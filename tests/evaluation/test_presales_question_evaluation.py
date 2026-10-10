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
from tests.evaluation.test_presales_strict_output_evaluation import inputs as inputs

from enterprise_doc_core.config import ModelProvider, ModelSettings


@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("many_gaps", [False, True])
async def test_question_collector_and_scorers_bind_complete_input_and_original_result(
    tmp_path, inputs, invalid, many_gaps
):
    if many_gaps:
        dataset = json.loads(inputs[0].read_bytes())
        dataset["requirements"][0]["text"] = "说明数据驻留区域。说明适用版本。"
        write_json(inputs[0], dataset)
        gold = json.loads(inputs[1].read_bytes())
        gold["datasetSha256"] = hashlib.sha256(inputs[0].read_bytes()).hexdigest()
        write_json(inputs[1], gold)

    def respond(request):
        body = json.loads(request.content)
        wire = json.loads(body["messages"][1]["content"])
        value = {
            "rules": [],
            "assessments": [],
            "status": "insufficient_evidence",
            "conclusion": "现有资料没有说明数据驻留区域。",
            "responses": [
                {
                    "requirementPartId": p["requirementPartId"],
                    "answer": "现有资料没有说明数据驻留区域。",
                    "citations": [],
                    "missingInformation": [
                        f"请确认第{index}部分的第{gap}项驻留信息。" for gap in range(7)
                    ]
                    if many_gaps
                    else ["请补充数据驻留区域说明。"],
                }
                for index, p in enumerate(wire["requirementParts"])
            ],
        }
        if invalid:
            value["responses"][0]["requirementPartId"] = "foreign"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]
            },
        )

    run = tmp_path / "question.json"
    report = await collect(
        inputs[0],
        run,
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="gpt-6-luna",
        ),
        model_route="primary",
        question_assessment=True,
        transport=httpx.MockTransport(respond),
    )
    assert report["schemaVersion"] == "presales-gateway-run-v11"
    row = report["observations"][0]
    assert row["provenance"]["promptVersion"] == "presales.v24"
    assert row["providerRequests"] == 1
    assert score(*inputs, report)["acceptedDrafts"] == (0 if invalid else 1)
    if many_gaps and not invalid:
        legacy = copy.deepcopy(report)
        legacy["observations"][0]["provenance"]["promptVersion"] = "presales.v21"
        with pytest.raises(ValueError, match="at most 12 items"):
            score(*inputs, legacy)
        legacy["observations"][0]["state"] = "failed"
        legacy["observations"][0].pop("result")
        assert score(*inputs, legacy)["acceptedDrafts"] == 0
    expected, review = tmp_path / "expected.json", tmp_path / "review.json"
    write_json(
        expected,
        {
            "schemaVersion": "presales-prerequisite-gold-v1",
            "datasetSha256": report["datasetSha256"],
            "reviewStatus": "Controlled fixture",
            "rows": [{"key": "R1", "prerequisites": []}],
        },
    )
    write_json(
        review,
        {
            "schemaVersion": "presales-prerequisite-review-v1",
            "runSha256": hashlib.sha256(run.read_bytes()).hexdigest(),
            "expectationsSha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
            "reviewer": "test",
            "reviewType": "assistant",
            "rows": [{"key": "R1", "reviewed": not invalid, "mappings": []}],
        },
    )
    assert (
        score_prerequisites(*inputs, run, expected, review)["prerequisiteCheckPassed"]
        is not invalid
    )
    for field in ("text", "requirementPartId"):
        tampered = copy.deepcopy(report)
        tampered["observations"][0]["traces"][0]["input"]["requirementParts"][0][field] = "changed"
        with pytest.raises(ValueError, match="wire_question_projection_mismatch"):
            score(*inputs, tampered)
    old = copy.deepcopy(report)
    old["schemaVersion"] = "presales-gateway-run-v10"
    with pytest.raises(ValueError, match="strict_output_contract_mismatch"):
        score(*inputs, old)
