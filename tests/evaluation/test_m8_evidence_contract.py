from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "evidence" / "index.json"
MANIFEST_PATH = "evidence/m8/20260720-054000-m8-end-to-end-model-deadline.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict[str, object]:
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


def _git_blob(commit_sha: str, relative_path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit_sha}:{relative_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def test_m8_reviewed_local_evidence_is_indexed() -> None:
    index = _load(INDEX_PATH)
    entries = index["evidence"]
    assert isinstance(entries, list)
    m8_entries = [entry for entry in entries if entry["milestone"] == "M8"]
    assert len(m8_entries) == 1
    entry = m8_entries[0]
    assert entry["milestone"] == "M8"
    assert entry["status"] == "passed"
    assert entry["manifest"] == MANIFEST_PATH

    manifest = _load(_artifact_path(MANIFEST_PATH))
    assert manifest["status"] == "passed"
    assert manifest["requirement_ids"] == [f"M8-R{number}" for number in range(1, 8)]
    commit_sha = manifest["commit_sha"]
    assert isinstance(commit_sha, str) and SHA_PATTERN.fullmatch(commit_sha)
    reviewed_commit = manifest["reviewed_commit"]
    evidence_commit = manifest["evidence_commit"]
    manifest_commit = manifest["manifest_commit"]
    assert isinstance(reviewed_commit, str) and SHA_PATTERN.fullmatch(reviewed_commit)
    assert isinstance(evidence_commit, str) and SHA_PATTERN.fullmatch(evidence_commit)
    assert isinstance(manifest_commit, str) and SHA_PATTERN.fullmatch(manifest_commit)
    assert commit_sha == reviewed_commit
    assert reviewed_commit != evidence_commit
    assert manifest_commit not in {reviewed_commit, evidence_commit}
    assert entry["manifest_commit"] == manifest_commit
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "cat-file", "-e", f"{evidence_commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "cat-file", "-e", f"{manifest_commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for relative_path in (MANIFEST_PATH, "evidence/index.json"):
        subprocess.run(
            ["git", "cat-file", "-e", f"{manifest_commit}:{relative_path}"],
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


def test_m8_evidence_artifacts_are_immutable_and_scope_is_local() -> None:
    manifest = _load(_artifact_path(MANIFEST_PATH))
    reviewed_commit = str(manifest["reviewed_commit"])
    evidence_commit = str(manifest["evidence_commit"])
    commands = manifest["command_or_procedure"]
    assert isinstance(commands, list) and commands
    assert all(result["exit_code"] == 0 for result in commands)

    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    for artifact in artifacts:
        relative_path = str(artifact["path"])
        artifact_commit = str(artifact["commit_sha"])
        assert artifact_commit in {reviewed_commit, evidence_commit}
        digest = hashlib.sha256(_git_blob(artifact_commit, relative_path)).hexdigest()
        assert artifact["sha256"] == digest

    limitations = manifest["limitations"]
    assert isinstance(limitations, list) and limitations
    joined = " ".join(str(item) for item in limitations).lower()
    assert "real-provider" in joined
    assert "production capacity" in joined
    assert manifest["manual_gates"] == []
