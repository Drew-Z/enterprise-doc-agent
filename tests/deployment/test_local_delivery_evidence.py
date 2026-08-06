from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "evidence" / "index.json"
MANIFEST_PATH = "evidence/m6/20260719-235533-m6-local-delivery-verification.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _repo_path(relative_path: str) -> Path:
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


def test_reviewed_local_delivery_verification_is_additive() -> None:
    index = _load(INDEX_PATH)
    evidence = index["evidence"]
    assert isinstance(evidence, list)
    formal_m6 = next(item for item in evidence if item["milestone"] == "M6")
    local_m6 = next(item for item in evidence if item["milestone"] == "M6-local-verification")
    assert formal_m6["status"] == "blocked_external"
    assert local_m6 == {
        "milestone": "M6-local-verification",
        "status": "passed",
        "manifest": MANIFEST_PATH,
    }

    manifest = _load(_repo_path(MANIFEST_PATH))
    assert manifest["status"] == "passed"
    assert manifest["working_tree_dirty"] is False
    commit_sha = manifest["commit_sha"]
    assert isinstance(commit_sha, str) and SHA_PATTERN.fullmatch(commit_sha)
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    started_at = datetime.fromisoformat(str(manifest["started_at"]))
    completed_at = datetime.fromisoformat(str(manifest["completed_at"]))
    assert started_at.tzinfo is not None
    assert completed_at.tzinfo is not None
    assert completed_at >= started_at


def test_current_m6_status_links_live_staging_and_keeps_remaining_recovery_open() -> None:
    index = _load(INDEX_PATH)
    formal_m6 = next(item for item in index["evidence"] if item["milestone"] == "M6")
    current = _load(_repo_path(str(formal_m6["current_status"])))
    host = _load(_repo_path(str(formal_m6["host_observation"])))

    assert current["status"] == "blocked_external"
    assert current["deployment"]["status"] == "passed"
    assert current["deployment"]["authenticated_smoke"] == "passed"
    assert current["host"]["workloads_ready"] is True
    assert current["host"]["firewall"] == "active"
    assert current["recovery"]["database_r2_bidirectional_validation"] == "passed"
    assert current["recovery"]["isolated_application_recovery_smoke"] == "passed"
    assert current["recovery"]["production_rpo_rto"] == "blocked_external"
    assert current["recovery"]["latest_evidence"] == formal_m6[
        "isolated_application_recovery_smoke"
    ]
    assert host["status"] == "passed_staging_host"
    assert host["cluster"]["node_status"] == "Ready"
    assert host["cluster"]["profile"] == "single-node-4c8g"
    assert host["workloads"]["api_ready"] == "2/2"
    assert host["network_boundary"]["ufw_status"] == "active"
    assert "not a highly available production cluster" in host["limitations"][0]


def test_local_delivery_artifacts_match_reviewed_commit() -> None:
    manifest = _load(_repo_path(MANIFEST_PATH))
    commit_sha = str(manifest["commit_sha"])
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    for artifact in artifacts:
        relative_path = str(artifact["path"])
        digest = hashlib.sha256(_git_blob(commit_sha, relative_path)).hexdigest()
        assert artifact["sha256"] == digest

    images = manifest["local_images"]
    assert isinstance(images, list) and len(images) == 4
    assert all(IMAGE_ID_PATTERN.fullmatch(str(image["image_id"])) for image in images)
    assert all("uid=" in str(image["runtime_identity"]) for image in images)


def test_local_delivery_evidence_cannot_satisfy_external_m6_gates() -> None:
    manifest = _load(_repo_path(MANIFEST_PATH))
    commands = manifest["command_or_procedure"]
    assert isinstance(commands, list) and commands
    assert all(result["exit_code"] == 0 for result in commands)
    limitations = " ".join(str(item) for item in manifest["limitations"]).lower()
    for boundary in (
        "not registry digests",
        "not a cluster apply",
        "dry-run only",
        "no postgresql backup",
        "plans only",
        "blocked_external",
    ):
        assert boundary in limitations
    assert set(manifest["manual_gates"]) == {
        "evidence/gates/m6-registry-signed-images.json",
        "evidence/gates/m6-cluster-staging-rollout.json",
        "evidence/gates/m6-backup-restore-rollback.json",
    }
