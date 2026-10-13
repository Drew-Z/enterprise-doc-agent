from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from scripts.evaluate_presales_gateway import collect
from scripts.score_presales_gateway import score

from enterprise_doc_core.config import ModelProvider, ModelSettings


@pytest.mark.parametrize("trial", ["v5", "v6", "v7", "v7-windhub", "v8"])
def test_frozen_gateway_scores_remain_unchanged(trial: str) -> None:
    root = Path("evaluation/presales_quality_holdout_v3")
    report_path = root.with_suffix(f".{trial}-gateway.json")
    recorded = json.loads(root.with_suffix(f".{trial}-gateway.score.json").read_bytes())
    recorded.pop("originalRunSha256", None)
    assert (
        score(
            root.with_suffix(".json"),
            root.with_suffix(".gold.json"),
            json.loads(report_path.read_bytes()),
        )
        == recorded
    )


def test_gateway_score_keeps_initial_rejections_and_classifier_errors() -> None:
    root = Path("evaluation/presales_quality_holdout_v2")
    report = json.loads(root.with_suffix(".v4-gateway.json").read_bytes())
    result = score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)
    assert result["expectedRows"] == result["realProviderRequests"] == 6
    assert result["acceptedDrafts"] == 5 and result["statusMatches"] == 3
    assert result["unsafeAffirmatives"] == 0
    assert result["usage"]["total_tokens"] == 43202
    assert result["costAmount"] is None


@pytest.mark.parametrize("change", ["hash", "duplicate_row", "evidence", "requirement"])
def test_gateway_score_rejects_changed_trial_bindings(change: str) -> None:
    root = Path("evaluation/presales_quality_holdout_v2")
    report = copy.deepcopy(json.loads(root.with_suffix(".v4-gateway.json").read_bytes()))
    first = report["observations"][0]
    if change == "hash":
        report["datasetSha256"] = "0" * 64
    elif change == "duplicate_row":
        report["observations"].append(first)
    elif change == "evidence":
        first["traces"][0]["input"]["evidence"][0]["text"] = "Fabricated source."
    else:
        first["traces"][0]["input"]["requirement"]["text"] = "Different requirement."
    with pytest.raises(ValueError):
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)


@pytest.fixture
async def projected_run(tmp_path):
    async def respond(request):
        wire = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "conditional",
                                    "answer": "需确认前提。",
                                    "missingInformation": ["请确认验收是否完成。"],
                                    "prerequisites": [
                                        {
                                            "proposition": "验收已通过。",
                                            "uncertainty": "missing",
                                            "positive": [],
                                            "negative": [],
                                            "definition": [
                                                {
                                                    "citationId": wire["evidence"][0]["citationId"],
                                                    "text": wire["evidence"][0]["text"],
                                                }
                                            ],
                                            "unconfirmed": [],
                                        }
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
        )

    return await collect(
        Path("evaluation/presales_quality_holdout_v4.json"),
        tmp_path / "run.json",
        ModelSettings(
            fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
            fallback_base_url="https://model.invalid/v1",
            fallback_api_key=SecretStr("test-only"),
            fallback_model_name="test-model",
        ),
        transport=httpx.MockTransport(respond),
    )


async def test_projected_run_keeps_exact_wire_binding_and_original_failure(projected_run):
    root = Path("evaluation/presales_quality_holdout_v4")
    result = score(root.with_suffix(".json"), root.with_suffix(".gold.json"), projected_run)
    assert result["acceptedDrafts"] == result["validCitations"] == 6
    assert result["usage"]["total_tokens"] is None
    first = projected_run["observations"][0]
    first["state"] = "failed"
    first.pop("result")
    result = score(root.with_suffix(".json"), root.with_suffix(".gold.json"), projected_run)
    assert result["acceptedDrafts"] == 5 and result["realProviderRequests"] == 6


async def test_v5_reports_keep_historical_missing_question_contract(projected_run):
    report = copy.deepcopy(projected_run)
    report["schemaVersion"] = "presales-gateway-run-v5"
    for observation in report["observations"]:
        message = observation["traces"][0]["response"]["choices"][0]["message"]
        raw = json.loads(message["content"])
        item = raw["prerequisites"][0]
        item["citations"] = [{"citationId": q["citationId"]} for q in item.pop("definition")]
        item.pop("unconfirmed")
        raw.pop("missingInformation")
        message["content"] = json.dumps(raw)
        observation["result"]["draft"]["missingInformation"] = []
    root = Path("evaluation/presales_quality_holdout_v4")
    assert (
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)["acceptedDrafts"]
        == 6
    )
    report["schemaVersion"] = "presales-gateway-run-v6"
    with pytest.raises(ValueError):
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)


async def test_v4_reports_keep_the_original_condition_instead_of_new_projection(projected_run):
    report = copy.deepcopy(projected_run)
    report["schemaVersion"] = "presales-gateway-run-v4"
    for observation in report["observations"]:
        message = observation["traces"][0]["response"]["choices"][0]["message"]
        raw = json.loads(message["content"])
        item = raw["prerequisites"][0]
        item["citations"] = [{"citationId": q["citationId"]} for q in item.pop("definition")]
        item.pop("unconfirmed")
        raw["prerequisites"][0]["condition"] = "旧协议要求补充验收记录。"
        message["content"] = json.dumps(raw)
        observation["result"]["draft"]["prerequisites"][0]["condition"] = "旧协议要求补充验收记录。"
        observation["result"]["draft"]["conditions"] = ["旧协议要求补充验收记录。"]
    root = Path("evaluation/presales_quality_holdout_v4")
    assert (
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)["acceptedDrafts"]
        == 6
    )
    report["schemaVersion"] = "presales-gateway-run-v5"
    with pytest.raises(ValueError):
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), report)


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "scope",
        "text",
        "label",
        "reference",
        "duplicate_reference",
        "omitted",
        "result",
        "raw_output",
        "prerequisite_state",
        "prerequisite_missing",
        "prerequisite_null",
        "prerequisite_reference",
    ],
)
async def test_projected_run_rejects_changed_source_wire_or_result(projected_run, change):
    first = projected_run["observations"][0]
    wire = first["traces"][0]["input"]["evidence"]
    if change == "source":
        first["sourceInput"]["evidence"][0]["chunkId"] = first["sourceInput"]["evidence"][1][
            "chunkId"
        ]
    elif change == "scope":
        wire[0]["source"]["applicability"] = "篡改范围"
    elif change == "text":
        wire[0]["text"] = "虚构原文"
    elif change == "label":
        wire[0]["source"]["label"] = wire[1]["source"]["label"]
    elif change == "reference":
        wire[0]["citationId"] = first["sourceInput"]["evidence"][0]["chunkId"]
    elif change == "duplicate_reference":
        wire[1]["citationId"] = wire[0]["citationId"]
    elif change == "omitted":
        wire.pop()
    elif change == "result":
        first["result"]["draft"]["conditions"] = ["已经完成。"]
    elif change == "prerequisite_state":
        first["result"]["draft"]["prerequisites"][0]["state"] = "unmet"
    elif change == "prerequisite_missing":
        first["result"]["draft"].pop("prerequisites")
    elif change == "prerequisite_null":
        first["result"]["draft"]["prerequisites"] = None
    elif change == "prerequisite_reference":
        first["result"]["draft"]["prerequisites"][0]["citationIndexes"] = [1]
    else:
        first["traces"][0]["response"]["choices"][0]["message"]["content"] = "{}"
    root = Path("evaluation/presales_quality_holdout_v4")
    with pytest.raises(ValueError):
        score(root.with_suffix(".json"), root.with_suffix(".gold.json"), projected_run)
