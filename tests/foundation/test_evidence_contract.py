from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "evidence" / "index.json"
MANIFEST_PATTERN = re.compile(r"^evidence/m0/\d{8}-\d{6}-m0-project-foundation\.json$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


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


def test_m0_evidence_manifest_is_indexed_and_complete() -> None:
    index = _load_json(INDEX_PATH)
    assert index["schema_version"] == 1
    entries = index["evidence"]
    assert isinstance(entries, list)
    m0_entries = [entry for entry in entries if entry["milestone"] == "M0"]
    assert len(m0_entries) == 1

    entry = m0_entries[0]
    manifest_relative = entry["manifest"]
    assert isinstance(manifest_relative, str)
    assert MANIFEST_PATTERN.fullmatch(manifest_relative)
    assert entry["status"] == "passed"

    manifest = _load_json(_artifact_path(manifest_relative))
    assert manifest["evidence_id"] == "m0-project-foundation"
    assert manifest["milestone"] == "M0"
    assert manifest["status"] == "passed"
    assert manifest["requirement_ids"] == [f"R-{number}" for number in range(1, 23)]
    assert manifest["owner"] == "Codex"

    commit_sha = manifest["commit_sha"]
    assert isinstance(commit_sha, str) and SHA_PATTERN.fullmatch(commit_sha)
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    started_at = datetime.fromisoformat(str(manifest["started_at"]))
    completed_at = datetime.fromisoformat(str(manifest["completed_at"]))
    assert started_at.tzinfo is not None
    assert completed_at.tzinfo is not None
    assert completed_at >= started_at

    assert manifest["environment"]
    assert set(manifest["tool_versions"]) >= {"python", "uv", "node", "pnpm", "docker"}
    assert manifest["result_summary"]
    assert manifest["image_digest"] == "not_applicable"


def test_every_evidence_command_and_artifact_is_materialized() -> None:
    index = _load_json(INDEX_PATH)
    manifest_path = index["evidence"][0]["manifest"]
    manifest = _load_json(_artifact_path(str(manifest_path)))

    commands = manifest["command_or_procedure"]
    assert isinstance(commands, list) and commands
    for result in commands:
        assert result["command"]
        assert result["exit_code"] == 0
        artifact = _artifact_path(result["artifact"])
        assert artifact.stat().st_size > 0

    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    for item in artifacts:
        path = _artifact_path(item["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert item["sha256"] == digest
        assert item["kind"] in {"log", "screenshot", "report"}

    limitations = manifest["limitations"]
    assert isinstance(limitations, list) and limitations
    assert any("M1-M7" in limitation for limitation in limitations)


def test_dashboard_visual_gate_records_both_reviewed_viewports() -> None:
    index = _load_json(INDEX_PATH)
    manifest = _load_json(_artifact_path(str(index["evidence"][0]["manifest"])))
    gates = manifest["visual_gates"]
    dashboard = next(gate for gate in gates if gate["gate_id"] == "dashboard-responsive")

    assert dashboard["result"] == "passed"
    assert dashboard["reviewer"] == "Codex"
    viewports = dashboard["viewports"]
    assert {(item["width"], item["height"]) for item in viewports} == {
        (1440, 900),
        (390, 844),
    }
    for item in viewports:
        _artifact_path(item["screenshot"])
