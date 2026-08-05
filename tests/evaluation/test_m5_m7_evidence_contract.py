from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
FORMAL_MANIFEST_PATTERNS = {
    "M5": re.compile(r"^evidence/m5/\d{8}-\d{6}-m5-observability-eval-load\.json$"),
    "M6": re.compile(r"^evidence/m6/\d{8}-\d{6}-m6-cicd-kubernetes\.json$"),
    "M7": re.compile(r"^evidence/m7/\d{8}-\d{6}-m7-local-model-routing\.json$"),
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _repo_path(relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    assert not pure.is_absolute()
    assert ".." not in pure.parts
    path = ROOT.joinpath(*pure.parts)
    assert path.is_file(), relative_path
    return path


def _git_blob(commit_sha: str, relative_path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit_sha}:{relative_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


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
        manifest_relative_path = str(entry["manifest"])
        manifest_path = _repo_path(manifest_relative_path)
        manifest = _load(manifest_path)
        assert manifest["status"] == "blocked_external"
        assert manifest["working_tree_dirty"] is False
        reviewed_commit = manifest["reviewed_commit"]
        evidence_commit = manifest["evidence_commit"]
        manifest_commit = manifest["manifest_commit"]
        assert isinstance(reviewed_commit, str) and SHA_PATTERN.fullmatch(reviewed_commit)
        assert isinstance(evidence_commit, str) and SHA_PATTERN.fullmatch(evidence_commit)
        assert isinstance(manifest_commit, str) and SHA_PATTERN.fullmatch(manifest_commit)
        assert reviewed_commit != evidence_commit
        assert manifest_commit not in {reviewed_commit, evidence_commit}
        assert manifest["commit_sha"] == reviewed_commit
        assert entry["manifest_commit"] == manifest_commit
        for commit_sha in (reviewed_commit, evidence_commit):
            subprocess.run(
                ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["git", "cat-file", "-e", f"{manifest_commit}^{{commit}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "cat-file", "-e", f"{manifest_commit}:{manifest_relative_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
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
            "reviewed_commit",
            "evidence_commit",
            "manifest_commit",
            "working_tree_dirty",
            "blocking_reason",
            "prerequisites",
        }
        assert required_fields <= set(manifest)
        started = datetime.fromisoformat(str(manifest["started_at"]))
        completed = datetime.fromisoformat(str(manifest["completed_at"]))
        assert started.tzinfo is not None
        assert completed >= started
        limitations = " ".join(str(item) for item in manifest["limitations"])
        assert "not" in limitations.lower()
        assert manifest["blocking_reason"]
        assert manifest["prerequisites"]
        artifacts = manifest["artifacts"]
        assert isinstance(artifacts, list) and artifacts
        for artifact in artifacts:
            relative_path = str(artifact["path"])
            path = _repo_path(relative_path)
            assert path.stat().st_size > 0
            artifact_commit = str(artifact["commit_sha"])
            assert artifact_commit in {reviewed_commit, evidence_commit}
            assert (
                artifact["sha256"]
                == hashlib.sha256(_git_blob(artifact_commit, relative_path)).hexdigest()
            )


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


def test_manual_gates_have_complete_state_records() -> None:
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
        "status",
        "review_date",
    }
    for relative in paths:
        gate = _load(ROOT / str(relative))
        assert required <= set(gate)
        gate_id = str(gate["gate_id"])
        assert gate_id not in gate_ids
        gate_ids.add(gate_id)
        date.fromisoformat(str(gate["review_date"]))
        assert gate["required_evidence"]
        match gate["state"]:
            case "open":
                assert gate["status"] == "blocked_external"
                assert gate["blocking_reason"]
                assert gate["prerequisites"]
            case "closed":
                assert gate["status"] == "passed"
                assert gate["blocking_reason"] is None
                completed_evidence = gate.get("completed_evidence")
                assert isinstance(completed_evidence, list) and completed_evidence
                for completed_relative in completed_evidence:
                    _repo_path(str(completed_relative))
            case state:
                raise AssertionError(f"unsupported manual gate state: {state!r}")

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
