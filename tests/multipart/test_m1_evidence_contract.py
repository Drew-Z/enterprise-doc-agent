from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "evidence" / "index.json"
REVIEWED_COMMIT = "ca43716265d7057aa79288bae054fc6ae0c5056d"
MANIFEST_PATTERN = re.compile(r"^evidence/m1/\d{8}-\d{6}-m1-multipart-upload\.json$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REQUIREMENTS = [f"R-{number}" for number in range(1, 27)]
EXPECTED_GATES = {
    "uv-sync",
    "pnpm-install",
    "backend-format",
    "backend-lint",
    "backend-typecheck",
    "backend-unit",
    "frontend-lint",
    "frontend-typecheck",
    "frontend-unit",
    "frontend-build",
    "multipart-integration",
    "multipart-smoke-1g",
    "playwright-recovery",
    "trellis-validation",
}
EXPECTED_SOURCE_URLS = {
    "https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html",
    "https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html",
    "https://docs.min.io/aistor/administration/cors-configuration",
    "https://docs.min.io/aistor/administration/object-lifecycle-management/lifecycle-rule-patterns",
    "https://github.com/minio/minio/issues/15874",
}
EXPECTED_SCREENSHOTS = {
    "evidence/m1/screenshots/upload-paused-1440x900.png",
    "evidence/m1/screenshots/wrong-file-390x844.png",
    "evidence/m1/screenshots/upload-complete-1440x900.png",
}
FORBIDDEN_TEXT_PATTERNS = {
    "authorization value": re.compile(r"(?i)\bauthorization\s*[:=]\s*(?!<|omitted)\S+"),
    "bearer token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{8,}"),
    "signed URL query": re.compile(
        r"(?i)(?:X-Amz-(?:Algorithm|Credential|Signature|Security-Token)|Signature=)"
    ),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "UUID": re.compile(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
    ),
    "resource identifier field": re.compile(
        r"(?i)\b(?:upload|session|document|version)(?:_|-)?id\b\s*[:=]"
    ),
    "object key field": re.compile(r"(?i)\bobject(?:_|-)?key\b\s*[:=]"),
    "filename field": re.compile(r"(?i)\bfilename\b\s*[:=]"),
    "content digest field": re.compile(r"(?i)\b(?:sha256|etag)\b\s*[:=]"),
    "raw 64-character digest": re.compile(r"(?i)\b[0-9a-f]{64}\b"),
    "Docker run identifier": re.compile(r"minio-init-run-[0-9a-f]{8,}"),
    "numeric process identifier": re.compile(r"\bPID\s+\d+\b"),
    "absolute Windows path": re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|workspace)[\\/]"),
}


def _load_json(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _evidence_path(relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    assert not pure_path.is_absolute()
    assert ".." not in pure_path.parts
    assert pure_path.parts[:2] == ("evidence", "m1")
    path = ROOT.joinpath(*pure_path.parts)
    assert path.is_file(), relative_path
    return path


def _m1_manifest() -> tuple[str, dict[str, object]]:
    index = _load_json(INDEX_PATH)
    assert index["schema_version"] == 1
    entries = index["evidence"]
    assert isinstance(entries, list)
    m1_entries = [entry for entry in entries if entry["milestone"] == "M1"]
    assert len(m1_entries) == 1
    entry = m1_entries[0]
    assert entry["status"] == "passed"
    manifest_relative = entry["manifest"]
    assert isinstance(manifest_relative, str)
    assert MANIFEST_PATTERN.fullmatch(manifest_relative)
    return manifest_relative, _load_json(_evidence_path(manifest_relative))


def _parse_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    assert parsed.tzinfo is not None
    return parsed


def test_m1_evidence_manifest_is_indexed_and_complete() -> None:
    _, manifest = _m1_manifest()
    assert manifest["evidence_id"] == "m1-multipart-upload"
    assert manifest["milestone"] == "M1"
    assert manifest["status"] == "passed"
    assert manifest["requirement_ids"] == EXPECTED_REQUIREMENTS
    assert manifest["owner"] == "Codex"

    commit_sha = manifest["commit_sha"]
    assert isinstance(commit_sha, str) and COMMIT_PATTERN.fullmatch(commit_sha)
    assert commit_sha == REVIEWED_COMMIT
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    started_at = _parse_timestamp(manifest["started_at"])
    completed_at = _parse_timestamp(manifest["completed_at"])
    assert completed_at >= started_at
    assert manifest["environment"]
    assert set(manifest["tool_versions"]) >= {
        "python",
        "uv",
        "node",
        "pnpm",
        "docker",
        "docker_compose",
    }
    assert manifest["result_summary"]
    assert manifest["image_digest"] == "not_applicable"

    source_urls = manifest["source_urls"]
    assert isinstance(source_urls, list)
    assert set(source_urls) == EXPECTED_SOURCE_URLS
    for source_url in source_urls:
        parsed_url = urlparse(source_url)
        assert parsed_url.scheme == "https"
        assert parsed_url.netloc


def test_every_m1_command_and_artifact_is_materialized_and_digested() -> None:
    manifest_relative, manifest = _m1_manifest()
    commands = manifest["command_or_procedure"]
    assert isinstance(commands, list) and commands
    gate_ids = [item["gate_id"] for item in commands]
    assert set(gate_ids) == EXPECTED_GATES
    assert len(gate_ids) == len(set(gate_ids))

    command_artifacts: set[str] = set()
    for result in commands:
        assert result["command"]
        assert result["exit_code"] == 0
        artifact_relative = result["artifact"]
        assert isinstance(artifact_relative, str)
        artifact = _evidence_path(artifact_relative)
        assert artifact.stat().st_size > 0
        command_artifacts.add(artifact_relative)

    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list) and artifacts
    artifact_paths = [item["path"] for item in artifacts]
    assert len(artifact_paths) == len(set(artifact_paths))
    assert manifest_relative not in artifact_paths
    assert command_artifacts <= set(artifact_paths)

    for item in artifacts:
        path = _evidence_path(item["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert isinstance(item["sha256"], str)
        assert DIGEST_PATTERN.fullmatch(item["sha256"])
        assert item["sha256"] == digest
        assert item["kind"] in {"log", "screenshot", "report"}

    materialized = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "evidence" / "m1").rglob("*")
        if path.is_file() and path.relative_to(ROOT).as_posix() != manifest_relative
    }
    assert set(artifact_paths) == materialized

    limitations = manifest["limitations"]
    assert isinstance(limitations, list) and limitations
    assert any("not a load test" in limitation for limitation in limitations)
    assert any("M2-M7" in limitation for limitation in limitations)
    assert any("whole-file SHA-256" in limitation for limitation in limitations)


def test_m1_smoke_report_records_bounded_direct_upload_recovery() -> None:
    _, manifest = _m1_manifest()
    report_artifact = next(item for item in manifest["artifacts"] if item["kind"] == "report")
    report = _load_json(_evidence_path(report_artifact["path"]))
    assert report["schema_version"] == 1
    assert report["milestone"] == "M1"
    assert report["result"] == "passed"
    assert report["generated_content"] == {
        "materialized_on_client_disk": False,
        "media_type": "text/plain",
        "size_bytes": 1_073_741_824,
    }
    assert report["direct_transfer"] == {
        "byte_plane": "client-to-object-store-presigned-put",
        "control_plane": "FastAPI",
    }
    assert report["multipart"] == {
        "expected_part_count": 64,
        "interrupted_after_parts": 2,
        "observed_parts_before_resume": 2,
        "part_size_bytes": 16_777_216,
        "uploaded_parts_after_resume": 62,
    }
    assert report["restart_count"] == 1
    assert report["first_completion_replayed"] is False
    assert report["completion_retry_replayed"] is True

    api_rss = report["api_rss"]
    assert api_rss["measured"] is True
    assert api_rss["sample_count"] > 0
    assert 0 < api_rss["min_bytes"] <= api_rss["max_bytes"]
    assert 0 <= api_rss["max_generation_delta_bytes"] <= api_rss["max_bytes"]
    assert api_rss["sampling_interval_seconds"] > 0
    assert _parse_timestamp(report["completed_at"]) >= _parse_timestamp(report["started_at"])
    assert report["limitations"]


def test_m1_visual_gate_records_reviewed_recovery_states() -> None:
    _, manifest = _m1_manifest()
    gates = manifest["visual_gates"]
    browser_gate = next(gate for gate in gates if gate["gate_id"] == "browser-upload-recovery")
    assert browser_gate["result"] == "passed"
    assert browser_gate["reviewer"] == "Codex"
    viewports = browser_gate["viewports"]
    assert {(item["width"], item["height"], item["state"]) for item in viewports} == {
        (1440, 900, "paused"),
        (390, 844, "wrong-file"),
        (1440, 900, "complete"),
    }
    screenshots = {item["screenshot"] for item in viewports}
    assert screenshots == EXPECTED_SCREENSHOTS
    for screenshot in screenshots:
        _evidence_path(screenshot)


def test_m1_text_evidence_excludes_secrets_and_per_run_identifiers() -> None:
    text_artifacts = sorted((ROOT / "evidence" / "m1" / "artifacts").glob("*"))
    for artifact in text_artifacts:
        if artifact.suffix not in {".json", ".log", ".md", ".txt"}:
            continue
        content = artifact.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_TEXT_PATTERNS.items():
            assert pattern.search(content) is None, f"{label} leaked in {artifact.name}"
