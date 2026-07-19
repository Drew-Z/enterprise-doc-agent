from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "evidence" / "index.json"
MANIFEST_PATTERN = re.compile(r"^evidence/m4/\d{8}-\d{6}-m4-agent-mcp-hitl-working-tree\.json$")
FORMAL_MANIFEST_PATTERN = re.compile(r"^evidence/m4/\d{8}-\d{6}-m4-agent-mcp-hitl\.json$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _load_json(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _artifact_path(relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    assert not pure_path.is_absolute()
    assert ".." not in pure_path.parts
    path = ROOT.joinpath(*pure_path.parts)
    assert path.is_file(), relative_path
    return path


def _m4_manifest() -> dict[str, object]:
    index = _load_json(INDEX_PATH)
    assert index["schema_version"] == 1
    entries = index["evidence"]
    assert isinstance(entries, list)
    m4_entries = [entry for entry in entries if entry["milestone"] == "M4"]
    assert len(m4_entries) == 1
    entry = m4_entries[0]
    assert entry["status"] == "blocked_external"
    manifest_path = entry["manifest"]
    assert isinstance(manifest_path, str)
    assert FORMAL_MANIFEST_PATTERN.fullmatch(manifest_path)
    return _load_json(_artifact_path(manifest_path))


def _m4_capture() -> dict[str, object]:
    index = _load_json(INDEX_PATH)
    captures = index["working_tree_captures"]
    assert isinstance(captures, list)
    entry = next(item for item in captures if item["milestone"] == "M4")
    assert entry["status"] == "unreviewed"
    capture_path = entry["capture"]
    assert isinstance(capture_path, str)
    assert MANIFEST_PATTERN.fullmatch(capture_path)
    return _load_json(_artifact_path(capture_path))


def test_m4_formal_manifest_is_blocked_until_reviewed_evidence_exists() -> None:
    manifest = _m4_manifest()
    assert manifest["evidence_id"] == "m4-agent-mcp-hitl"
    assert manifest["milestone"] == "M4"
    assert manifest["status"] == "blocked_external"
    assert manifest["working_tree_dirty"] is True
    assert manifest["commit_sha"] is None
    assert manifest["reviewed_commit"] is None
    assert manifest["evidence_commit"] is None
    gates = manifest["manual_gates"]
    assert gates == ["evidence/gates/m4-reviewed-immutable-evidence.json"]

    capture = _m4_capture()
    assert capture["status"] == "working-tree"

    base_commit = capture["base_commit"]
    assert isinstance(base_commit, str) and SHA_PATTERN.fullmatch(base_commit)
    subprocess.run(
        ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    started_at = datetime.fromisoformat(str(capture["started_at"]))
    completed_at = datetime.fromisoformat(str(capture["completed_at"]))
    assert started_at.tzinfo is not None
    assert completed_at.tzinfo is not None
    assert completed_at >= started_at

    limitations = manifest["limitations"]
    assert isinstance(limitations, list)
    limitation_text = " ".join(str(item) for item in limitations).lower()
    assert "commit" in limitation_text
    assert "production model" in limitation_text


def test_m4_manifest_commands_and_hashes_are_materialized() -> None:
    manifest = _m4_manifest()
    commands = manifest["command_or_procedure"]
    assert isinstance(commands, list) and commands
    for result in commands:
        assert result["gate_id"]
        if result["gate_id"] == "m4-working-tree-local-matrix":
            assert result["exit_code"] == 0
        else:
            assert result["status"] == "blocked_external"
        artifact = _artifact_path(str(result["artifact"]))
        assert artifact.stat().st_size > 0

    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    for item in artifacts:
        path = _artifact_path(str(item["path"]))
        assert path.stat().st_size > 0
        assert item["kind"] in {
            "log",
            "report",
            "screenshot",
            "manual-gate",
            "working-tree-capture",
        }
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    capture = _m4_capture()
    visual_gates = capture["visual_gates"]
    assert isinstance(visual_gates, list)
    responsive = next(
        gate for gate in visual_gates if gate["gate_id"] == "agent-workspace-responsive"
    )
    assert responsive["result"] == "passed"
    viewports = responsive["viewports"]
    assert {(item["width"], item["height"]) for item in viewports} == {(1440, 900), (390, 844)}
    for item in viewports:
        _artifact_path(str(item["screenshot"]))


def test_m4_safety_and_logs_are_sanitized() -> None:
    report = _load_json(ROOT / "evidence/m4/artifacts/m4-agent-safety-v1.json")
    assert report["passed"] is True
    assert report["summary"] == {"failed": 0, "passed": 13, "total": 13}

    for path in (ROOT / "evidence/m4/artifacts").glob("*.log"):
        content = path.read_text(encoding="utf-8")
        assert UUID_PATTERN.search(content) is None, path
        assert "local-token" not in content.lower(), path
        assert "m4-evaluation-secret" not in content, path
