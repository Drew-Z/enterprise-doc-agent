from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from scripts.evaluate_presales_gateway import collect
from scripts.score_presales_prerequisites import score_prerequisites

from enterprise_doc_core.config import ModelProvider, ModelSettings

ROOT = Path("evaluation/presales_quality_holdout_v3")
RUN = ROOT.with_suffix(".v7-windhub-gateway.json")


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def review_files(tmp_path: Path) -> tuple[Path, Path]:
    expected = {
        "schemaVersion": "presales-prerequisite-gold-v1",
        "datasetSha256": hashlib.sha256(ROOT.with_suffix(".json").read_bytes()).hexdigest(),
        "reviewStatus": "Assistant-authored regression, not independent adjudication",
        "rows": [
            {
                "key": f"H3-R{index}",
                "prerequisites": [
                    {
                        "key": key,
                        "description": description,
                        "state": state,
                        "requiredEvidence": [{"sourceKey": "H3-ORDER", "excerpt": anchor}],
                    }
                    for key, description, state, anchor in (
                        ("purchase", "购买归档模块", "met", "已采购历史归档 ARC180"),
                        ("configure", "配置保留策略", "unmet", "归档保留策略尚未配置"),
                        ("acceptance", "完成归档恢复验收", "unknown", "归档恢复验收状态未登记"),
                    )
                ]
                if index == 5
                else [],
            }
            for index in range(1, 7)
        ],
    }
    expected_path = write_json(tmp_path / "expected.json", expected)
    mapping = {
        "schemaVersion": "presales-prerequisite-review-v1",
        "runSha256": hashlib.sha256(RUN.read_bytes()).hexdigest(),
        "expectationsSha256": hashlib.sha256(expected_path.read_bytes()).hexdigest(),
        "reviewer": "test assistant",
        "reviewType": "assistant",
        "rows": [
            {
                "key": f"H3-R{index}",
                "reviewed": index >= 4,
                "mappings": [
                    {"expectedKey": key, "observedIndex": i, "reason": "原稿对应同一业务前提"}
                    for i, key in enumerate(("purchase", "configure", "acceptance"))
                ]
                if index == 5
                else [],
            }
            for index in range(1, 7)
        ],
    }
    return expected_path, write_json(tmp_path / "review.json", mapping)


def test_frozen_v7_correct_classification_does_not_hide_wrong_prerequisite(tmp_path):
    expected, review = review_files(tmp_path)
    before = RUN.read_bytes()
    result = score_prerequisites(
        ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), RUN, expected, review
    )
    row = next(row for row in result["rows"] if row["key"] == "H3-R5")
    assert row["statusMatch"] is True
    assert row["stateMatches"] == 2
    assert row["stateMismatches"] == 1
    assert row["passed"] is False
    assert row["items"][2]["observedState"] == "unmet"
    assert row["items"][2]["expectedState"] == "unknown"
    assert result["expectedRows"] == result["realProviderRequests"] == 6
    assert result["observableRows"] == 3
    assert result["prerequisiteCheckPassed"] is False
    assert result["semanticReviewRequired"] is True
    assert result["independentDomainReview"] is False
    assert RUN.read_bytes() == before


def test_recorded_regression_analysis_is_reproducible():
    result = score_prerequisites(
        ROOT.with_suffix(".json"),
        ROOT.with_suffix(".gold.json"),
        RUN,
        ROOT.with_suffix(".prerequisites.json"),
        Path("evaluation/presales_quality_v7.windhub.prerequisite-review.json"),
    )
    recorded = Path("evaluation/presales_quality_v7.windhub.prerequisite-score.json")
    assert result == json.loads(recorded.read_bytes())


def test_cli_preserves_failed_score_and_returns_nonzero(tmp_path):
    expected, review = review_files(tmp_path)
    output = tmp_path / "score.json"
    command = [sys.executable, "-B", "-m", "scripts.score_presales_prerequisites"]
    for name, path in (
        ("input", ROOT.with_suffix(".json")),
        ("gold", ROOT.with_suffix(".gold.json")),
        ("run", RUN),
        ("expectations", expected),
        ("review", review),
        ("output", output),
    ):
        command.extend(("--" + name, str(path)))
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 1
    assert json.loads(output.read_bytes())["prerequisiteCheckPassed"] is False
    before = output.read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert repeated.returncode != 0
    assert output.read_bytes() == before


@pytest.fixture
async def complete_run(tmp_path):
    statuses = {
        row["key"]: row["status"]
        for row in json.loads(ROOT.with_suffix(".gold.json").read_bytes())["rows"]
    }

    async def respond(request):
        wire = json.loads(json.loads(request.content)["messages"][1]["content"])
        references = [{"citationId": item["citationId"]} for item in wire["evidence"]]
        draft = {
            "status": statuses[wire["requirement"]["key"]],
            "answer": "仅供受控接口测试的回复。不代表真实模型质量。",
            "prerequisites": [
                {"condition": condition, "state": state, "citations": references}
                for condition, state in (
                    ("确认归档恢复的验收结论", "unknown"),
                    ("模块已购入", "met"),
                    ("设置保留周期", "unmet"),
                )
            ]
            if wire["requirement"]["key"] == "H3-R5"
            else [],
            "missingInformation": ["请补充缺少的材料或澄清优先条款。"],
            "citations": references,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]
            },
        )

    run_path = tmp_path / "run.json"
    await collect(
        ROOT.with_suffix(".json"),
        run_path,
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://model.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="test-model",
        ),
        model_route="primary",
        transport=httpx.MockTransport(respond),
    )
    expected, review = review_files(tmp_path)
    mapping = json.loads(review.read_bytes())
    mapping["runSha256"] = hashlib.sha256(run_path.read_bytes()).hexdigest()
    for row in mapping["rows"]:
        row["reviewed"] = True
        for item, index in zip(row["mappings"], (1, 2, 0), strict=bool(row["mappings"])):
            item["observedIndex"] = index
    write_json(review, mapping)
    return run_path, expected, review


async def test_full_review_uses_explicit_mapping_not_order_or_condition_words(complete_run):
    result = score_prerequisites(
        ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), *complete_run
    )
    assert result["passedRows"] == result["expectedRows"] == 6
    assert result["prerequisiteCheckPassed"] is True
    assert result["semanticReviewRequired"] is True
    assert result["independentDomainReview"] is False
    assert result["rows"][4]["stateMatches"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "run_hash",
        "expected_hash",
        "dataset_hash",
        "duplicate_gold_row",
        "missing_gold_row",
        "duplicate_expected",
        "invalid_anchor",
        "unknown_anchor_source",
        "duplicate_review_row",
        "unknown_review_row",
        "missing_mapping",
        "duplicate_mapping",
        "unknown_mapping",
        "duplicate_index",
        "invalid_index",
        "boolean_index",
        "string_index",
        "empty_reason",
    ],
)
async def test_rejects_stale_or_ambiguous_review_bindings(complete_run, change):
    _, expected_path, review_path = complete_run
    expected = json.loads(expected_path.read_bytes())
    review = json.loads(review_path.read_bytes())
    items = expected["rows"][4]["prerequisites"]
    mappings = review["rows"][4]["mappings"]
    if change == "run_hash":
        review["runSha256"] = "0" * 64
    elif change == "expected_hash":
        review["expectationsSha256"] = "0" * 64
    elif change == "dataset_hash":
        expected["datasetSha256"] = "0" * 64
    elif change == "duplicate_gold_row":
        expected["rows"][0] = expected["rows"][1]
    elif change == "missing_gold_row":
        expected["rows"].pop()
    elif change == "duplicate_expected":
        items[1]["key"] = items[0]["key"]
    elif change == "invalid_anchor":
        items[0]["requiredEvidence"][0]["excerpt"] = "没有记载的事实"
    elif change == "unknown_anchor_source":
        items[0]["requiredEvidence"][0]["sourceKey"] = "UNKNOWN"
    elif change == "duplicate_review_row":
        review["rows"][0] = review["rows"][1]
    elif change == "unknown_review_row":
        review["rows"][0]["key"] = "OTHER"
    elif change == "missing_mapping":
        mappings.pop()
    elif change == "duplicate_mapping":
        mappings[1]["expectedKey"] = mappings[0]["expectedKey"]
    elif change == "unknown_mapping":
        mappings[0]["expectedKey"] = "OTHER"
    elif change == "duplicate_index":
        mappings[1]["observedIndex"] = mappings[0]["observedIndex"]
    elif change == "invalid_index":
        mappings[0]["observedIndex"] = 9
    elif change == "boolean_index":
        mappings[0]["observedIndex"] = True
    elif change == "string_index":
        mappings[0]["observedIndex"] = "1"
    elif change == "empty_reason":
        mappings[0]["reason"] = " "
    write_json(expected_path, expected)
    if change != "expected_hash":
        review["expectationsSha256"] = hashlib.sha256(expected_path.read_bytes()).hexdigest()
    write_json(review_path, review)
    with pytest.raises(ValueError):
        score_prerequisites(
            ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), *complete_run
        )


@pytest.mark.parametrize(
    "change", ["missing_item", "unreviewed", "missing_review", "wrong_evidence"]
)
async def test_incomplete_state_or_item_evidence_cannot_pass(complete_run, change):
    run, expected_path, review_path = complete_run
    expected = json.loads(expected_path.read_bytes())
    review = json.loads(review_path.read_bytes())
    if change == "missing_item":
        review["rows"][4]["mappings"][2]["observedIndex"] = None
    elif change == "unreviewed":
        review["rows"][4]["reviewed"] = False
    elif change == "missing_review":
        review["rows"].pop(4)
    else:
        # A real anchor that is absent from this prerequisite's bound citation set.
        old = json.loads(RUN.read_bytes())
        write_json(run, old)
        review["runSha256"] = hashlib.sha256(run.read_bytes()).hexdigest()
        for row in review["rows"][:3]:
            row["reviewed"] = False
        for item, index in zip(review["rows"][4]["mappings"], (0, 1, 2), strict=True):
            item["observedIndex"] = index
        expected["rows"][4]["prerequisites"][0]["requiredEvidence"] = [
            {"sourceKey": "H3-LIMIT", "excerpt": "单张巡检工单的附件数量硬上限为12个。"}
        ]
        write_json(expected_path, expected)
        review["expectationsSha256"] = hashlib.sha256(expected_path.read_bytes()).hexdigest()
    write_json(review_path, review)
    result = score_prerequisites(
        ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), *complete_run
    )
    assert result["expectedRows"] == 6 and result["prerequisiteCheckPassed"] is False
    row = result["rows"][4]
    assert row["passed"] is False
    if change == "missing_item":
        assert row["missingPrerequisites"] == 1
        assert row["unexpectedIndexes"] == [0]
    elif change == "wrong_evidence":
        assert row["items"][0]["stateMatch"] is True
        assert row["items"][0]["coveredRequiredEvidence"] == 0
    else:
        assert row["reviewed"] is False


@pytest.mark.parametrize("change", ["failed", "unattempted"])
async def test_failed_and_missing_attempts_stay_in_denominator(complete_run, change):
    run, _, review_path = complete_run
    report = json.loads(run.read_bytes())
    if change == "failed":
        report["observations"][4]["state"] = "failed"
        report["observations"][4].pop("result")
    else:
        report["observations"].pop(4)
    write_json(run, report)
    review = json.loads(review_path.read_bytes())
    review["runSha256"] = hashlib.sha256(run.read_bytes()).hexdigest()
    write_json(review_path, review)
    with pytest.raises(ValueError, match="cannot_review_failed_or_unattempted_output"):
        score_prerequisites(
            ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), *complete_run
        )
    review["rows"].pop(4)
    write_json(review_path, review)
    result = score_prerequisites(
        ROOT.with_suffix(".json"), ROOT.with_suffix(".gold.json"), *complete_run
    )
    assert result["observableRows"] == result["passedRows"] == 5
    assert result["expectedRows"] == 6 and result["prerequisiteCheckPassed"] is False
    assert result["realProviderRequests"] == (6 if change == "failed" else 5)
