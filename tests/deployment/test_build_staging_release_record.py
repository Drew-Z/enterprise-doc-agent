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


def _build(
    tmp_path: Path,
    *,
    outcomes: dict[str, str],
    smoke_required: bool,
    model_provider: str = "openai_compatible",
    model_base_url: str = "https://model.example.com/v1",
    model_name: str = "staging-model",
) -> dict[str, object]:
    return build_record(
        _manifest(tmp_path / "evidence.json"),
        deployment_profile="tiny-single-node",
        repository="example/repo",
        commit_sha="a" * 40,
        run_id="42",
        run_attempt="1",
        registry_prefix="registry.example/team",
        image_digests=_digests(),
        outcomes=outcomes,
        model_provider=model_provider,
        model_base_url=model_base_url,
        model_name=model_name,
        smoke_required=smoke_required,
        output=tmp_path / "record.json",
    )


def test_staging_record_passes_only_with_rollout_and_smoke(tmp_path: Path) -> None:
    record = _build(tmp_path, outcomes=_outcomes(), smoke_required=True)
    assert record["status"] == "passed"
    assert record["deployment_profile"] == "tiny-single-node"
    assert record["model"] == {
        "status": "validated",
        "provider": "openai_compatible",
        "base_url": "https://model.example.com/v1",
        "name": "staging-model",
    }
    assert record["embedding"] == {
        "status": "validated",
        "provider": "openai_compatible",
        "base_url": "https://embedding.example.invalid/v1",
        "name": "staging-embedding",
        "kind": "embedding",
        "dimension": 1024,
        "version": 2,
    }


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
            deployment_profile="tiny-single-node",
            repository="example/repo",
            commit_sha="a" * 40,
            run_id="42",
            run_attempt="1",
            registry_prefix="registry.example/team",
            image_digests=digests,
            outcomes=_outcomes(),
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
            smoke_required=True,
            output=tmp_path / "record.json",
        )


def test_staging_record_rejects_unreviewed_deployment_profile(tmp_path: Path) -> None:
    with pytest.raises(StagingReleaseRecordError, match="deployment profile"):
        build_record(
            _manifest(tmp_path / "evidence.json"),
            deployment_profile="arbitrary-overlay",
            repository="example/repo",
            commit_sha="a" * 40,
            run_id="42",
            run_attempt="1",
            registry_prefix="registry.example/team",
            image_digests=_digests(),
            outcomes=_outcomes(),
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
            smoke_required=True,
            output=tmp_path / "record.json",
        )


def test_staging_record_accepts_reviewed_single_node_4c8g_profile(tmp_path: Path) -> None:
    record = build_record(
        _manifest(tmp_path / "evidence.json"),
        deployment_profile="single-node-4c8g",
        repository="example/repo",
        commit_sha="a" * 40,
        run_id="42",
        run_attempt="1",
        registry_prefix="registry.example/team",
        image_digests=_digests(),
        outcomes=_outcomes(),
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        smoke_required=True,
        output=tmp_path / "record.json",
    )
    assert record["deployment_profile"] == "single-node-4c8g"
    assert record["status"] == "passed"


def test_staging_record_normalizes_non_secret_model_metadata(tmp_path: Path) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        model_base_url=" https://model.example.com/v1/ ",
        model_name=" staging-model ",
    )
    assert record["model"] == {
        "status": "validated",
        "provider": "openai_compatible",
        "base_url": "https://model.example.com/v1",
        "name": "staging-model",
    }


def test_staging_record_rejects_unreviewed_model_provider(tmp_path: Path) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        model_provider="deterministic",
    )
    assert record["status"] == "failed"
    assert record["model"] == {
        "status": "invalid",
        "provider": None,
        "base_url": None,
        "name": None,
        "configured": {"provider": True, "base_url": True, "name": True},
        "validation_error": "model provider must be openai_compatible",
    }


@pytest.mark.parametrize(
    "value",
    [
        "http://model.example.com/v1",
        "https://user:password@model.example.com/v1",
        "https://model.example.com/v1?key=value",
        "https://model.example.com/v1#fragment",
        "https://localhost/v1",
        "https://api.localhost/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.1/v1",
        "https://169.254.169.254/v1",
        "https://192.0.2.1/v1",
        "https://[fe80::1]/v1",
        "https://model.example.com:not-a-port/v1",
        "https://model.example.com/api",
        "https://model.example.com/v1\nignored",
    ],
)
def test_staging_record_rejects_unsafe_model_base_url(tmp_path: Path, value: str) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        model_base_url=value,
    )
    assert record["status"] == "failed"
    assert record["model"]["status"] == "invalid"
    assert record["model"]["base_url"] is None
    assert value not in json.dumps(record)


@pytest.mark.parametrize("value", [" ", "bad\nname", "x" * 201])
def test_staging_record_rejects_invalid_model_name(tmp_path: Path, value: str) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        model_name=value,
    )
    assert record["status"] == "failed"
    assert record["model"]["status"] == "invalid"
    assert record["model"]["name"] is None
    if value.strip():
        assert value not in json.dumps(record)


def test_staging_record_preserves_missing_model_configuration_as_failed_evidence(
    tmp_path: Path,
) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes("skipped"),
        smoke_required=True,
        model_base_url="",
        model_name="",
    )
    assert record["status"] == "failed"
    assert record["failure_reason"] == "Model routing validation failed before staging rollout."
    assert record["model"]["configured"] == {
        "provider": True,
        "base_url": False,
        "name": False,
    }
