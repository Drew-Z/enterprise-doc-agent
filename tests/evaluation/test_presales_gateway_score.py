from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts.score_presales_gateway import score


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
