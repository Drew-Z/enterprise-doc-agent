from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "evidence/m3/20260718-204755-m2-m3-hardening.json"


def test_m2_m3_hardening_manifest_is_reviewable_and_materialized() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["milestone"] == "m2-m3-hardening"
    assert payload["result"] == "passed"
    commit = payload["implementation_commit"]
    assert len(commit) == 40
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert payload["commands"]
    assert all(item["result"] == "passed" for item in payload["commands"])
    assert payload["implemented"]
    assert payload["limitations"]
    for relative_path in payload["artifacts"]:
        assert (ROOT / relative_path).is_file(), relative_path
