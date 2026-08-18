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


def _rollout_report(path: Path, *, final_selected: int = 0, version: int = 2) -> Path:
    plan = {
        "status": "planned",
        "selected": 0,
        "created": 0,
        "replayed": 0,
        "embedding_model": "staging-embedding",
        "embedding_dimension": 1024,
        "embedding_version": version,
        "values_redacted": True,
    }
    final = {**plan, "selected": final_selected}
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "failed_stage": None,
                "error_code": None,
                "probe": {
                    "status": "passed",
                    "provider": "openai_compatible",
                    "model": "staging-embedding",
                    "dimension": 1024,
                    "version": version,
                    "item_count": 2,
                    "finite": True,
                    "nonzero_norms": True,
                    "values_redacted": True,
                },
                "reindex": {
                    "status": "completed",
                    "initial_plan": plan,
                    "attempts": [],
                    "final_plan": final,
                },
                "values_redacted": True,
            }
        ),
        encoding="utf-8",
    )
    return path


def _outcomes(value: str = "success") -> dict[str, str]:
    return {
        "prerequisites": value,
        "migration": value,
        "workloads": value,
        "rollout": value,
        "embedding_rollout": value,
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
    fallback_model_base_url: str | None = "https://fallback.example.com/v1",
    fallback_model_name: str | None = "fallback-model",
    fallback_model_version: str | None = "2026-08-17",
    fallback_model_timeout_seconds: str | None = "60",
    embedding_version: int = 2,
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
        fallback_model_base_url=fallback_model_base_url,
        fallback_model_name=fallback_model_name,
        fallback_model_version=fallback_model_version,
        fallback_model_timeout_seconds=fallback_model_timeout_seconds,
        embedding_rollout_report=_rollout_report(
            tmp_path / "embedding-rollout.json", version=embedding_version
        ),
        embedding_version=embedding_version,
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
    assert record["schema_version"] == 3
    assert record["fallback_model"] == {
        "status": "validated",
        "provider": "openai_compatible",
        "base_url": "https://fallback.example.com/v1",
        "name": "fallback-model",
        "version": "2026-08-17",
        "timeout_seconds": 60.0,
    }
    assert record["embedding"]["status"] == "validated"
    assert record["embedding"]["dimension"] == 1024
    assert record["embedding"]["version"] == 2
    assert record["embedding"]["rollout"]["reindex"]["final_selected"] == 0
    assert record["embedding"]["rollout_report"]["sha256"]


def test_staging_record_preserves_alternate_embedding_version(tmp_path: Path) -> None:
    record = _build(tmp_path, outcomes=_outcomes(), smoke_required=True, embedding_version=3)

    assert record["embedding"]["version"] == 3
    assert record["embedding"]["rollout"]["version"] == 3


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


def test_staging_record_marks_embedding_gate_failure_failed(tmp_path: Path) -> None:
    outcomes = _outcomes()
    outcomes["embedding_rollout"] = "failure"
    assert _build(tmp_path, outcomes=outcomes, smoke_required=True)["status"] == "failed"


def test_staging_record_rejects_nonconverged_embedding_report(tmp_path: Path) -> None:
    report = _rollout_report(tmp_path / "nonconverged.json", final_selected=1)
    record = build_record(
        _manifest(tmp_path / "evidence.json"),
        deployment_profile="tiny-single-node",
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
        embedding_rollout_report=report,
        smoke_required=True,
        output=tmp_path / "record.json",
    )
    assert record["status"] == "failed"
    assert record["embedding"]["status"] == "invalid"
    assert record["embedding"]["validation_error"] == "embedding reindex did not converge"


def test_staging_record_preserves_missing_embedding_report_as_failed_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "record.json"
    outcomes = _outcomes()
    outcomes["embedding_rollout"] = "failure"
    record = build_record(
        _manifest(tmp_path / "evidence.json"),
        deployment_profile="tiny-single-node",
        repository="example/repo",
        commit_sha="a" * 40,
        run_id="42",
        run_attempt="1",
        registry_prefix="registry.example/team",
        image_digests=_digests(),
        outcomes=outcomes,
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        embedding_rollout_report=tmp_path / "missing.json",
        smoke_required=True,
        output=output,
    )
    assert record["status"] == "failed"
    assert record["embedding"]["status"] == "invalid"
    assert record["embedding"]["validation_error"] == ("embedding rollout report does not exist")
    assert output.is_file()


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
            embedding_rollout_report=_rollout_report(tmp_path / "embedding-rollout.json"),
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
            embedding_rollout_report=_rollout_report(tmp_path / "embedding-rollout.json"),
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
        embedding_rollout_report=_rollout_report(tmp_path / "embedding-rollout.json"),
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


def test_staging_record_preserves_unconfigured_fallback_route(tmp_path: Path) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        fallback_model_base_url=None,
        fallback_model_name=None,
        fallback_model_version=None,
        fallback_model_timeout_seconds=None,
    )

    assert record["status"] == "passed"
    assert record["fallback_model"] == {
        "status": "not_configured",
        "provider": None,
        "base_url": None,
        "name": None,
        "version": None,
        "timeout_seconds": None,
    }


@pytest.mark.parametrize("timeout", [None, "0", "301", "not-a-number"])
def test_staging_record_rejects_invalid_fallback_timeout(
    tmp_path: Path,
    timeout: str | None,
) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        fallback_model_timeout_seconds=timeout,
    )

    assert record["status"] == "failed"
    assert record["fallback_model"]["status"] == "invalid"
    assert record["fallback_model"]["timeout_seconds"] is None


def test_staging_record_rejects_partial_fallback_route(tmp_path: Path) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        fallback_model_name=None,
    )

    assert record["status"] == "failed"
    assert record["fallback_model"]["status"] == "invalid"
    assert record["fallback_model"]["configured"]["base_url"] is True
    assert record["fallback_model"]["configured"]["name"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fallback_model_base_url", "https://fallback.example.com/v1\n"),
        ("fallback_model_name", "fallback-model\n"),
        ("fallback_model_version", "2026-08-17\n"),
        ("fallback_model_timeout_seconds", "60\n"),
    ],
)
def test_staging_record_rejects_fallback_control_characters(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    record = _build(
        tmp_path,
        outcomes=_outcomes(),
        smoke_required=True,
        fallback_model_base_url=(
            value if field == "fallback_model_base_url" else "https://fallback.example.com/v1"
        ),
        fallback_model_name=(value if field == "fallback_model_name" else "fallback-model"),
        fallback_model_version=(value if field == "fallback_model_version" else "2026-08-17"),
        fallback_model_timeout_seconds=(
            value if field == "fallback_model_timeout_seconds" else "60"
        ),
    )

    assert record["status"] == "failed"
    assert record["fallback_model"]["status"] == "invalid"
