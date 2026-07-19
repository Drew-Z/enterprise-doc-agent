from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.build_staging_release_record import StagingReleaseRecordError, build_record


def _manifest(path: Path) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "files": [{"path": "x"}]}), encoding="utf-8")
    return path


def _digests() -> dict[str, str]:
    return {
        name: f"sha256:{value * 64}"
        for name, value in zip(("api", "worker", "consumer", "web"), "abcd", strict=True)
    }


def _outcomes(value: str = "success") -> dict[str, str]:
    return {
        "prerequisites": value,
        "migration": value,
        "workloads": value,
        "rollout": value,
        "cluster_smoke": value,
        "authenticated_smoke": value,
    }


def _build(tmp_path: Path, *, outcomes: dict[str, str], smoke_required: bool) -> dict[str, object]:
    return build_record(
        _manifest(tmp_path / "evidence.json"),
        repository="example/repo",
        commit_sha="a" * 40,
        run_id="42",
        run_attempt="1",
        registry_prefix="registry.example/team",
        image_digests=_digests(),
        outcomes=outcomes,
        smoke_required=smoke_required,
        output=tmp_path / "record.json",
    )


def test_staging_record_passes_only_with_rollout_and_smoke(tmp_path: Path) -> None:
    assert _build(tmp_path, outcomes=_outcomes(), smoke_required=True)["status"] == "passed"


def test_staging_record_marks_skipped_smoke_blocked_external(tmp_path: Path) -> None:
    outcomes = _outcomes()
    outcomes["cluster_smoke"] = "skipped"
    outcomes["authenticated_smoke"] = "skipped"
    record = _build(tmp_path, outcomes=outcomes, smoke_required=False)
    assert record["status"] == "blocked_external"
    assert record["blocking_reason"]


def test_staging_record_marks_rollout_failure_failed(tmp_path: Path) -> None:
    outcomes = _outcomes()
    outcomes["migration"] = "failure"
    assert _build(tmp_path, outcomes=outcomes, smoke_required=True)["status"] == "failed"


def test_staging_record_rejects_missing_digest(tmp_path: Path) -> None:
    digests = _digests()
    del digests["web"]
    with pytest.raises(StagingReleaseRecordError, match="digests"):
        build_record(
            _manifest(tmp_path / "evidence.json"),
            repository="example/repo",
            commit_sha="a" * 40,
            run_id="42",
            run_attempt="1",
            registry_prefix="registry.example/team",
            image_digests=digests,
            outcomes=_outcomes(),
            smoke_required=True,
            output=tmp_path / "record.json",
        )
