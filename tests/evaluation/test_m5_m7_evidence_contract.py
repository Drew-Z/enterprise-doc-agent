from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMAL_MANIFEST_PATTERNS = {
    "M5": re.compile(r"^evidence/m5/\d{8}-\d{6}-m5-observability-eval-load\.json$"),
    "M6": re.compile(r"^evidence/m6/\d{8}-\d{6}-m6-cicd-kubernetes\.json$"),
    "M7": re.compile(r"^evidence/m7/\d{8}-\d{6}-m7-local-model-routing\.json$"),
}


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_m5_m6_m7_formal_manifests_are_indexed_and_hashed() -> None:
    index = _load(ROOT / "evidence/index.json")
    assert index["schema_version"] == 1
    entries = index.get("evidence")
    assert isinstance(entries, list)
    by_milestone = {str(item["milestone"]): item for item in entries}
    for milestone in ("M5", "M6", "M7"):
        entry = by_milestone[milestone]
        assert entry["status"] == "blocked_external"
        assert FORMAL_MANIFEST_PATTERNS[milestone].fullmatch(str(entry["manifest"]))
        manifest_path = ROOT / str(entry["manifest"])
        manifest = _load(manifest_path)
        assert manifest["status"] == "blocked_external"
        assert manifest["working_tree_dirty"] is True
        assert manifest["commit_sha"] is None
        assert manifest["reviewed_commit"] is None
        assert manifest["evidence_commit"] is None
        required_fields = {
            "evidence_id",
            "milestone",
            "requirement_ids",
            "status",
            "command_or_procedure",
            "environment",
            "commit_sha",
            "image_digest",
            "started_at",
            "completed_at",
            "result_summary",
            "artifacts",
            "limitations",
            "owner",
        }
        assert required_fields <= set(manifest)
        started = datetime.fromisoformat(str(manifest["started_at"]))
        completed = datetime.fromisoformat(str(manifest["completed_at"]))
        assert started.tzinfo is not None
        assert completed >= started
        limitations = " ".join(str(item) for item in manifest["limitations"])
        assert "not" in limitations.lower()
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list) and artifacts
        for artifact in artifacts:
            path = ROOT / str(artifact["path"])
            assert path.is_file() and path.stat().st_size > 0
            assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_working_tree_captures_are_separate_from_formal_evidence() -> None:
    index = _load(ROOT / "evidence/index.json")
    captures = index["working_tree_captures"]
    assert isinstance(captures, list)
    assert {item["milestone"] for item in captures} == {"M4", "M5", "M6", "M7"}
    for item in captures:
        assert item["status"] == "unreviewed"
        capture = _load(ROOT / str(item["capture"]))
        assert capture["status"] == "working-tree"
        assert capture["working_tree_dirty"] is True


def test_external_gates_have_complete_open_records() -> None:
    index = _load(ROOT / "evidence/index.json")
    paths = index["manual_gates"]
    assert isinstance(paths, list) and paths
    gate_ids: set[str] = set()
    required = {
        "gate_id",
        "requirement",
        "owner",
        "blocking_reason",
        "prerequisites",
        "required_evidence",
        "state",
        "review_date",
    }
    for relative in paths:
        gate = _load(ROOT / str(relative))
        assert required <= set(gate)
        gate_id = str(gate["gate_id"])
        assert gate_id not in gate_ids
        gate_ids.add(gate_id)
        assert gate["state"] == "open"
        assert gate["status"] == "blocked_external"
        date.fromisoformat(str(gate["review_date"]))
        assert gate["prerequisites"]
        assert gate["required_evidence"]

    for milestone in ("M5", "M6", "M7"):
        manifest = _load(
            ROOT
            / str(
                next(
                    item["manifest"] for item in index["evidence"] if item["milestone"] == milestone
                )
            )
        )
        assert set(manifest["manual_gates"]) <= set(paths)
