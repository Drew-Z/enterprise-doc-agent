from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "evidence/m2/20260718-173000-m2-durable-job-runtime.json"


def test_m2_manifest_has_reviewable_evidence_contract() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["milestone"] == "m2-durable-job-runtime"
    assert payload["result"] == "passed"
    assert len(payload["implementation_commit"]) == 7
    assert payload["commands"]
    assert all(item["result"] == "passed" for item in payload["commands"])
    assert payload["artifacts"]
    assert payload["limitations"]
