from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.run_analysis_capacity import (
    AnalysisCapacityError,
    CapacityIO,
    _release_identity,
    build_capacity_report,
    load_capacity_plan,
    validate_capacity_plan,
    validate_resource_snapshots,
)

ROOT = Path(__file__).resolve().parents[2]
DIGESTS = {
    service: "sha256:" + character * 64
    for service, character in zip(
        ("api", "worker", "consumer", "web"), ("a", "b", "c", "d"), strict=True
    )
}
CORRELATION_ID = "analysis-capacity:test-run"
CORRELATION_SHA256 = hashlib.sha256(
    json.dumps(CORRELATION_ID, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile": "analysis-4c4g",
        "base_url": "http://127.0.0.1:8000",
        "object_origins": ["http://127.0.0.1:9000"],
        "token_env": "ANALYSIS_CAPACITY_TOKEN",
        "repetitions": 2,
        "target": {
            "cpu_cores": 4,
            "memory_bytes": 4 * 1024**3,
            "p95_ms": 120000,
            "max_error_rate": 0.01,
            "min_throughput_per_second": 0.01,
        },
        "analysis_case": {
            "project_id": "10000000-0000-0000-0000-000000000001",
            "snapshot_id": "20000000-0000-0000-0000-000000000002",
            "execution_mode": "fixed",
            "units_per_run": 6,
            "max_dispatches_per_unit": 2,
        },
        "parse_fixture": {
            "path": (
                ".trellis/tasks/10-11-document-analysis-workspace/materials/"
                "validation-01/upload/requirements.synthetic.txt"
            ),
            "media_type": "text/plain",
            "sha256": "e79b9639fb43fe3b53b57705b20155fe2e07dc8d0e8c0dd2002a1e2b2f628a4f",
            "bytes": 855,
        },
        "max_model_dispatches": 168,
        "phases": [
            {
                "name": "ramp",
                "analysis_runs": 1,
                "parse_uploads": 1,
                "concurrency": 1,
                "cancel_runs": 0,
                "replay_runs": 0,
            },
            {
                "name": "steady_state",
                "analysis_runs": 2,
                "parse_uploads": 2,
                "concurrency": 2,
                "cancel_runs": 0,
                "replay_runs": 1,
            },
            {
                "name": "burst",
                "analysis_runs": 3,
                "parse_uploads": 2,
                "concurrency": 4,
                "cancel_runs": 1,
                "replay_runs": 1,
            },
            {
                "name": "recovery",
                "analysis_runs": 1,
                "parse_uploads": 1,
                "concurrency": 1,
                "cancel_runs": 0,
                "replay_runs": 1,
            },
        ],
        "prometheus": {
            "base_url": "http://127.0.0.1:9090",
            "queries": {
                "cpu": "test_cpu",
                "memory": "test_memory",
                "database_pool": "test_database_pool",
                "database_lock": "test_database_lock",
                "queue": "test_queue",
                "redis": "test_redis",
                "object_store": "test_object_store",
                "model": "test_model",
            },
        },
    }


def test_example_plan_is_exact_4c4g_and_covers_contention_cancel_and_recovery() -> None:
    loaded = load_capacity_plan(ROOT / "infra/capacity/analysis-4c4g.example.yaml", root=ROOT)

    assert loaded.target.cpu_cores == 4
    assert loaded.target.memory_bytes == 4 * 1024**3
    assert [phase.name for phase in loaded.phases] == [
        "ramp",
        "steady_state",
        "burst",
        "recovery",
    ]
    assert all(phase.analysis_runs and phase.parse_uploads for phase in loaded.phases)
    assert sum(phase.cancel_runs for phase in loaded.phases) > 0
    assert sum(phase.replay_runs for phase in loaded.phases) > 0
    assert loaded.planned_max_dispatches <= loaded.max_model_dispatches <= 200


def test_plan_only_cli_is_runnable_by_file_path() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_analysis_capacity.py"),
            "--plan",
            str(ROOT / "infra/capacity/analysis-4c4g.example.yaml"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        text=True,
        timeout=30,
    )

    payload = json.loads(completed.stdout)
    assert payload == {
        "status": "validated",
        "profile": "analysis-4c4g",
        "planned_analysis_runs": 14,
        "planned_parse_uploads": 12,
        "planned_max_dispatches": 168,
        "production_capacity_approved": False,
    }


def test_plan_rejects_missing_recovery_behavior_and_dispatch_budget() -> None:
    payload = _plan()
    payload["phases"][-1]["replay_runs"] = 0  # type: ignore[index]
    with pytest.raises(AnalysisCapacityError, match="replay"):
        validate_capacity_plan(payload, root=ROOT)

    payload = _plan()
    payload["max_model_dispatches"] = 100
    with pytest.raises(AnalysisCapacityError, match="dispatch"):
        validate_capacity_plan(payload, root=ROOT)


def test_capacity_io_adds_one_stable_target_binding_marker_to_every_api_request() -> None:
    import asyncio

    import httpx

    plan = validate_capacity_plan(_plan(), root=ROOT)
    io = CapacityIO(plan, "token", correlation_id=CORRELATION_ID)
    captured: dict[str, object] = {}

    async def request(*args: object, **kwargs: object) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return httpx.Response(200)

    io.api.request = request  # type: ignore[method-assign]
    asyncio.run(io.api_request(object(), "GET", "/", headers={"Idempotency-Key": "one"}))

    assert captured["headers"] == {
        "Idempotency-Key": "one",
        "X-Correlation-ID": CORRELATION_ID,
    }


def test_local_report_keeps_targets_measurements_and_nonproduction_boundary() -> None:
    plan = validate_capacity_plan(_plan(), root=ROOT)
    samples = [
        {
            "phase": "ramp",
            "kind": "analysis",
            "status": "passed",
            "duration_ms": 1000.0,
        },
        {
            "phase": "steady_state",
            "kind": "parse",
            "status": "passed",
            "duration_ms": 2000.0,
        },
        {
            "phase": "burst",
            "kind": "analysis_cancel",
            "status": "passed",
            "duration_ms": 3000.0,
        },
        {
            "phase": "recovery",
            "kind": "analysis_replay",
            "status": "passed",
            "duration_ms": 4000.0,
        },
    ]
    telemetry = {
        name: {"sample_count": 2, "p95": 50.0, "max": 60.0}
        for name in (
            "cpu",
            "memory",
            "database_pool",
            "database_lock",
            "queue",
            "redis",
            "object_store",
            "model",
        )
    }

    report = build_capacity_report(
        plan=plan,
        samples=samples,
        telemetry=telemetry,
        duration_seconds=10.0,
        external_execution=False,
        environment={"name": "local-docker-4c4g"},
    )

    assert report["status"] == "blocked_external"
    assert report["targets"]["cpu_cores"] == 4
    assert report["targets"]["memory_bytes"] == 4 * 1024**3
    assert report["measured"]["p50_ms"] == 2000.0
    assert report["measured"]["p95_ms"] == 4000.0
    assert report["measured"]["p99_ms"] == 4000.0
    assert report["measured"]["error_rate"] == 0.0
    assert report["measured"]["throughput_per_second"] == 0.4
    assert "database_lock" in report["measured"]["telemetry"]
    assert report["capacity_conclusion"].endswith("It is not a production capacity claim.")
    assert any("single" in item.lower() for item in report["limitations"])


def _resource_snapshot(
    captured_at: datetime, *, cpu: int = 4, observations: int = 0
) -> dict[str, object]:
    deployments = []
    pods = []
    for role in ("api", "worker", "consumer", "web", "redis"):
        digest = DIGESTS.get(role, "sha256:" + "e" * 64)
        deployments.append(
            {
                "role": role,
                "uid_sha256": role + "-deployment",
                "template_sha256": role + "-template",
                "generation": 1,
                "observed_generation": 1,
                "desired_replicas": 1,
                "ready_replicas": 1,
                "updated_replicas": 1,
                "containers": [{"name": role, "configured_digest": digest, "limits": {}}],
            }
        )
        pods.append(
            {
                "role": role,
                "uid_sha256": role + "-pod",
                "phase": "Running",
                "containers": [
                    {
                        "name": role,
                        "configured_digest": digest,
                        "running_digest": digest,
                        "ready": True,
                        "restarts": 0,
                        "last_oom": False,
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "collection_started_at": (captured_at - timedelta(seconds=1)).isoformat(),
        "captured_at": captured_at.isoformat(),
        "namespace_sha256": "namespace",
        "inventory": {
            "cluster_sha256": "cluster",
            "configuration_sha256": "configuration",
            "nodes": [
                {
                    "uid_sha256": "node",
                    "cpu_capacity": str(cpu),
                    "memory_capacity": "4Gi",
                }
            ],
            "deployments": deployments,
            "pods": pods,
            "image_relationships": [],
        },
        "host": {
            "cpu_count": cpu,
            "memory_total_bytes": 4 * 1024**3,
            "memory_available_bytes": 3 * 1024**3,
            "cpu_total_ticks": 100,
            "cpu_idle_ticks": 50,
            "root_disk_total_bytes": 20 * 1024**3,
            "root_disk_available_bytes": 10 * 1024**3,
        },
        "node_metrics": [],
        "pod_metrics": [],
        "scrapes": [],
        "traffic_proof": {
            "status": "observed" if observations else "not_observed",
            "correlation_sha256": CORRELATION_SHA256,
            "api_pod_uid_sha256": "api-pod",
            "observations": observations,
        },
        "errors": [],
    }


def test_external_resource_snapshots_bind_4c4g_and_release_identity() -> None:
    plan = validate_capacity_plan(_plan(), root=ROOT)
    started = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
    completed = started + timedelta(seconds=20)
    snapshots = [
        _resource_snapshot(started - timedelta(seconds=1)),
        _resource_snapshot(completed + timedelta(seconds=1), observations=42),
    ]
    release = {"image_digests": DIGESTS}

    evidence = validate_resource_snapshots(
        snapshots,
        plan=plan,
        release=release,
        started_at=started,
        completed_at=completed,
        interval_seconds=30,
        correlation_id=CORRELATION_ID,
        expected_http_requests=42,
    )

    assert evidence["status"] == "verified"
    assert evidence["sample_count"] == 2
    assert evidence["cpu_cores"] == 4
    assert evidence["memory_bytes"] == 4 * 1024**3
    assert evidence["target_binding"]["origin"] == "http://127.0.0.1:8000"
    assert evidence["target_binding"]["observations"] == 42


def test_external_resource_snapshot_rejects_declared_target_without_machine_proof() -> None:
    plan = validate_capacity_plan(_plan(), root=ROOT)
    started = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
    snapshots = [
        _resource_snapshot(started - timedelta(seconds=1), cpu=8),
        _resource_snapshot(started + timedelta(seconds=21), cpu=8),
    ]
    with pytest.raises(AnalysisCapacityError, match="cpu_mismatch"):
        validate_resource_snapshots(
            snapshots,
            plan=plan,
            release={"image_digests": DIGESTS},
            started_at=started,
            completed_at=started + timedelta(seconds=20),
            interval_seconds=30,
            correlation_id=CORRELATION_ID,
            expected_http_requests=0,
        )


def test_external_resource_snapshot_rejects_partial_or_unrelated_api_traffic() -> None:
    plan = validate_capacity_plan(_plan(), root=ROOT)
    started = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
    completed = started + timedelta(seconds=20)
    snapshots = [
        _resource_snapshot(started - timedelta(seconds=1)),
        _resource_snapshot(completed + timedelta(seconds=1), observations=41),
    ]
    with pytest.raises(AnalysisCapacityError, match="target_not_observed"):
        validate_resource_snapshots(
            snapshots,
            plan=plan,
            release={"image_digests": DIGESTS},
            started_at=started,
            completed_at=completed,
            interval_seconds=30,
            correlation_id=CORRELATION_ID,
            expected_http_requests=42,
        )

    snapshots[-1]["traffic_proof"]["correlation_sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(AnalysisCapacityError, match="target_binding_invalid"):
        validate_resource_snapshots(
            snapshots,
            plan=plan,
            release={"image_digests": DIGESTS},
            started_at=started,
            completed_at=completed,
            interval_seconds=30,
            correlation_id=CORRELATION_ID,
            expected_http_requests=42,
        )


def test_release_manifest_binds_commit_and_all_service_digests(tmp_path: Path) -> None:
    commit = "f" * 40
    release_path = tmp_path / "release.json"
    release_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "release": {
                    "repository": "example/enterprise-doc-agent",
                    "ref": "refs/tags/v1",
                    "commit_sha": commit,
                    "run_id": "42",
                    "run_attempt": "1",
                    "workflow_ref": "example/workflow@refs/tags/v1",
                },
                "images": {service: {"digest": digest} for service, digest in DIGESTS.items()},
            }
        ),
        encoding="utf-8",
    )

    identity = _release_identity(release_path, deployed_commit=commit, root=tmp_path)
    assert identity["commit_sha"] == commit
    assert identity["repository"] == "example/enterprise-doc-agent"
    assert identity["ref"] == "refs/tags/v1"
    assert identity["run_id"] == "42"
    assert identity["run_attempt"] == "1"
    assert identity["workflow_ref"] == "example/workflow@refs/tags/v1"
    assert identity["image_digests"] == DIGESTS
    with pytest.raises(AnalysisCapacityError, match="identity_mismatch"):
        _release_identity(release_path, deployed_commit="0" * 40, root=tmp_path)
