from __future__ import annotations

import hashlib
import json

import pytest


def test_metrics_keep_only_bounded_capacity_fields_without_fabricating_missing_values():
    from scripts.business_capacity_telemetry import parse_metrics

    result = parse_metrics("""
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes 1024
# TYPE enterprise_doc_queue_oldest_age_seconds gauge
enterprise_doc_queue_oldest_age_seconds NaN
# TYPE enterprise_doc_dependency_duration_seconds histogram
enterprise_doc_dependency_duration_seconds_count{dependency="model",result="success"} 2
enterprise_doc_dependency_duration_seconds_sum{dependency="model",result="success"} 3.5
enterprise_doc_dependency_duration_seconds_bucket{dependency="model",result="success",le="5.0"} 2
enterprise_doc_dependency_duration_seconds_bucket{dependency="model",result="success",le="+Inf"} 2
unrelated_metric{tenant_id="secret-tenant"} 99
""")
    assert result["gauges"]["process_resident_memory_bytes"] == 1024
    assert result["gauges"]["enterprise_doc_queue_oldest_age_seconds"] is None
    assert result["gauges"]["enterprise_doc_redis_connections"] is None
    assert result["dependencies"]["model"]["success"]["count"] == 2
    assert "secret-tenant" not in json.dumps(result)
    assert "enterprise_doc_queue_oldest_age_seconds" in result["nonfinite"]


def test_metrics_reject_sensitive_labels_on_selected_metrics():
    from scripts.business_capacity_telemetry import parse_metrics

    with pytest.raises(ValueError, match="metric_labels_rejected"):
        parse_metrics('process_resident_memory_bytes{tenant_id="secret"} 1')


def inventory_payloads():
    digest = "sha256:" + "a" * 64
    deployments, pods = [], []
    for role in ("api", "worker", "consumer", "web", "redis"):
        name = "enterprise-doc-" + role
        deployments.append(
            {
                "metadata": {"name": name, "uid": role, "generation": 1},
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": role,
                                    "image": "registry.invalid/image@" + digest,
                                    "env": [{"name": "PRIVATE", "value": "secret-config"}],
                                    "resources": {"limits": {"cpu": "500m", "memory": "512Mi"}},
                                }
                            ]
                        }
                    },
                },
                "status": {"readyReplicas": 1, "updatedReplicas": 1, "observedGeneration": 1},
            }
        )
        pods.append(
            {
                "metadata": {"uid": "pod-" + role, "labels": {"app.kubernetes.io/name": name}},
                "spec": {"containers": [{"name": role, "image": "image@" + digest}]},
                "status": {
                    "phase": "Running",
                    "podIP": "10.42.0.1",
                    "containerStatuses": [
                        {
                            "name": role,
                            "ready": True,
                            "imageID": "image@" + digest,
                            "restartCount": 0,
                        }
                    ],
                },
            }
        )
    nodes = [
        {
            "metadata": {"uid": "node-1", "name": "private-host"},
            "status": {"capacity": {"cpu": "4", "memory": "4Gi"}},
        }
    ]
    config = {"data": {"PRIVATE": "secret-config"}}
    return deployments, pods, nodes, config


def test_host_projection_binds_actual_images_config_limits_and_preserves_missing_status():
    from scripts.capacity_host_probe import project_inventory

    deployments, pods, nodes, config = inventory_payloads()
    result = project_inventory(deployments, pods, nodes, config)
    encoded = json.dumps(result)
    assert "secret-config" not in encoded and "private-host" not in encoded
    assert "10.42.0.1" not in encoded
    assert result["deployments"][0]["containers"][0]["limits"]["cpu"] == "500m"
    assert result["pods"][0]["containers"][0]["running_digest"] == "sha256:" + "a" * 64
    original = result["configuration_sha256"]
    config["data"]["PRIVATE"] = "changed"
    assert project_inventory(deployments, pods, nodes, config)["configuration_sha256"] != original
    pods[0]["status"].pop("containerStatuses")
    updated = project_inventory(deployments, pods, nodes, config)
    assert (
        next(p for p in updated["pods"] if p["role"] == "api")["containers"][0]["running_digest"]
        is None
    )


def snapshot(at="2026-09-25T00:00:05+00:00"):
    from scripts.capacity_host_probe import project_inventory

    inventory = project_inventory(*inventory_payloads())
    return {
        "schema_version": 1,
        "collection_started_at": at,
        "captured_at": at,
        "namespace_sha256": "n" * 64,
        "inventory": inventory,
        "host": {
            "cpu_count": 4,
            "memory_total_bytes": 4 * 1024**3,
            "memory_available_bytes": 2 * 1024**3,
            "cpu_total_ticks": 100,
            "cpu_idle_ticks": 80,
            "root_disk_total_bytes": 100000,
            "root_disk_available_bytes": 60000,
        },
        "node_metrics": [
            {
                "uid_sha256": inventory["nodes"][0]["uid_sha256"],
                "timestamp": at,
                "window": "20s",
                "usage": {"cpu": "100000000n", "memory": "2Gi"},
            }
        ],
        "pod_metrics": [
            {
                "uid_sha256": p["uid_sha256"],
                "timestamp": at,
                "window": "20s",
                "containers": [{"name": p["role"], "usage": {"cpu": "20m", "memory": "64Mi"}}],
            }
            for p in inventory["pods"]
        ],
        "scrapes": [
            {
                "role": p["role"],
                "uid_sha256": p["uid_sha256"],
                "scraped_at": at,
                "text": "process_resident_memory_bytes 1024\nprocess_cpu_seconds_total 2\n"
                "process_start_time_seconds 1\nenterprise_doc_database_pool_utilization_percent 5\n"
                "enterprise_doc_queue_oldest_age_seconds 0\nenterprise_doc_redis_connections 1\n",
                "error_code": None,
            }
            for p in inventory["pods"]
            if p["role"] in ("api", "worker", "consumer")
        ],
        "errors": [],
    }


def test_observations_preserve_stale_missing_and_drift_without_capacity_approval():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    first = normalize_snapshot(snapshot())
    changed = snapshot("2026-09-25T00:01:05+00:00")
    changed["node_metrics"][0]["timestamp"] = "2026-09-25T00:00:00+00:00"
    changed["inventory"]["configuration_sha256"] = "b" * 64
    records = [
        {"index": 0, "status": "observed", "snapshot": first},
        {"index": 1, "status": "observed", "snapshot": normalize_snapshot(changed)},
        {"index": 2, "status": "failed", "error_code": "ssh_failed"},
    ]
    report = summarize_observations(records, interval_seconds=5)
    assert report["planned_samples"] == 3 and report["observed_samples"] == 2
    assert report["status"] == "observation_incomplete"
    assert "deployment_identity_drift" in report["issues"]
    assert "stale_node_metrics" in report["issues"]
    assert report["production_capacity_approved"] is False
    assert "text" not in json.dumps(first)


def test_repeated_kubernetes_samples_are_counted_as_one_and_no_work_is_not_zero_latency():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    first = normalize_snapshot(snapshot())
    later = snapshot("2026-09-25T00:00:10+00:00")
    later["node_metrics"][0]["timestamp"] = first["node_metrics"][0]["timestamp"]
    later["host"]["cpu_total_ticks"] += 100
    later["host"]["cpu_idle_ticks"] += 75
    later = normalize_snapshot(later)
    report = summarize_observations(
        [{"index": n, "status": "observed", "snapshot": s} for n, s in enumerate([first, later])],
        interval_seconds=5,
    )
    assert report["unique_node_metric_samples"] == 1
    assert report["host"]["cpu_busy_percent_max"] == 25
    assert report["dependencies"]["model"]["observed_events"] is None
    assert report["dependencies"]["model"]["mean_seconds"] is None


def test_collection_is_opt_in_bounded_and_preserves_all_failed_samples(tmp_path, monkeypatch):
    import scripts.collect_business_telemetry as collector
    from scripts.collect_business_telemetry import ObservationPlan, run_collection

    calls = []

    def unavailable(plan):
        calls.append(plan.ssh_host)
        raise RuntimeError("secret-host-detail")

    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)
    result = run_collection(
        ObservationPlan(ssh_host="known-alias", samples=2), tmp_path / "new", probe=unavailable
    )
    assert len(calls) == 2
    assert result["summary"]["planned_samples"] == 2
    assert result["summary"]["observed_samples"] == 0
    assert len(result["records"]) == 2
    assert "secret-host-detail" not in json.dumps(result)
    with pytest.raises(ValueError, match="output_directory_rejected"):
        run_collection(
            ObservationPlan(ssh_host="known-alias", samples=2), tmp_path / "new", probe=unavailable
        )
    assert len(calls) == 2


def test_telemetry_cli_dry_run_does_not_connect_or_create_output(tmp_path):
    import subprocess
    import sys

    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        "-m",
        "scripts.collect_business_telemetry",
        "--ssh-host",
        "nonexistent.example.invalid",
        "--output-dir",
        str(tmp_path / "absent"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dry_run"] is True
    assert not (tmp_path / "absent").exists()


def test_image_index_is_verified_for_the_actual_platform_without_equating_two_digests():
    from scripts.capacity_host_probe import verify_image_relationship

    running = "sha256:" + "a" * 64
    payload = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": running,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    ).encode()
    configured = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert verify_image_relationship(configured, running, "amd64", read_content=lambda _: payload)[
        "verified"
    ]
    assert not verify_image_relationship(
        configured, running, "arm64", read_content=lambda _: payload
    )["verified"]
    assert not verify_image_relationship(
        configured, running, "amd64", read_content=lambda _: payload + b" "
    )["verified"]


def test_runtime_archive_index_must_bind_both_declared_index_and_selected_manifest():
    from scripts.capacity_host_probe import verify_image_relationship

    child = "sha256:" + "a" * 64
    raw = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": child,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    ).encode()
    configured = "sha256:" + hashlib.sha256(raw).hexdigest()
    archive = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {"digest": configured, "mediaType": "application/vnd.oci.image.index.v1+json"},
                {"digest": child, "mediaType": "application/vnd.oci.image.manifest.v1+json"},
            ],
        }
    ).encode()
    running = "sha256:" + hashlib.sha256(archive).hexdigest()
    content = {configured: raw, running: archive}
    result = verify_image_relationship(
        configured, running, "amd64", read_content=content.__getitem__
    )
    assert result["verified"] and result["kind"] == "verified_runtime_archive_index"
    assert not verify_image_relationship(
        configured, running, "arm64", read_content=content.__getitem__
    )["verified"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_configured",
        "missing_platform",
        "wrong_type",
        "duplicate_reference",
        "wrong_schema",
        "not_an_index",
        "malformed_member",
        "tampered_bytes",
        "ambiguous_platform",
        "invalid_child_digest",
    ],
)
def test_runtime_archive_rejects_unproven_relationships(fault):
    from scripts.capacity_host_probe import verify_image_relationship

    child = "sha256:" + "a" * 64
    index_type = "application/vnd.oci.image.index.v1+json"
    manifest_type = "application/vnd.oci.image.manifest.v1+json"
    manifest = {
        "digest": child,
        "mediaType": manifest_type,
        "platform": {"architecture": "amd64", "os": "linux"},
    }
    manifests = [manifest]
    if fault == "ambiguous_platform":
        manifests.append({**manifest, "digest": "sha256:" + "b" * 64})
    if fault == "invalid_child_digest":
        manifest["digest"] = "not-a-digest"
    raw = json.dumps({"schemaVersion": 2, "mediaType": index_type, "manifests": manifests}).encode()
    configured = "sha256:" + hashlib.sha256(raw).hexdigest()
    members = [
        {"digest": configured, "mediaType": index_type},
        {"digest": child, "mediaType": manifest_type},
    ]
    archive = {"schemaVersion": 2, "manifests": members}
    if fault == "missing_configured":
        members.pop(0)
    elif fault == "missing_platform":
        members.pop(1)
    elif fault == "wrong_type":
        members[0]["mediaType"] = manifest_type
    elif fault == "duplicate_reference":
        members.append(members[0])
    elif fault == "wrong_schema":
        archive["schemaVersion"] = 1
    elif fault == "not_an_index":
        archive["mediaType"] = manifest_type
    elif fault == "malformed_member":
        members.append(None)
    archive_raw = json.dumps(archive).encode()
    running = "sha256:" + hashlib.sha256(archive_raw).hexdigest()
    if fault == "tampered_bytes":
        archive_raw += b" "
    result = verify_image_relationship(
        configured,
        running,
        "amd64",
        read_content={configured: raw, running: archive_raw}.__getitem__,
    )
    assert result["verified"] is False and result["kind"] == "unresolved"


def test_probe_only_uses_read_commands_and_scrapes_every_application_replica(monkeypatch):
    import copy
    import io
    import subprocess
    import urllib.request
    from pathlib import Path
    from types import SimpleNamespace

    import scripts.capacity_host_probe as probe

    deployments, pods, nodes, config = inventory_payloads()
    child = "sha256:" + "a" * 64
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": child,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    ).encode()
    configured = "sha256:" + hashlib.sha256(index).hexdigest()
    deployments[0]["spec"]["replicas"] = 2
    deployments[0]["status"].update(readyReplicas=2, updatedReplicas=2)
    pods.append(copy.deepcopy(pods[0]))
    nodes[0]["status"]["nodeInfo"] = {"architecture": "amd64"}
    for deployment in deployments:
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "image@" + configured
    for i, pod in enumerate(pods):
        pod["metadata"].update(name=f"private-pod-{i}", uid=f"private-pod-uid-{i}")
        pod["spec"]["containers"][0]["image"] = "image@" + configured
        pod["status"]["podIP"] = f"10.42.0.{i + 1}"
    ns = "enterprise-doc-agent-staging"
    payloads = {
        ("get", "nodes", "-o", "json"): {"items": nodes},
        ("-n", ns, "get", "deployments", "-o", "json"): {"items": deployments},
        ("-n", ns, "get", "pods", "-o", "json"): {"items": pods},
        ("-n", ns, "get", "configmap", "enterprise-doc-config", "-o", "json"): config,
        ("get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"): {"items": []},
        ("get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods"): {"items": []},
    }
    commands, urls = [], []

    def run(command, *, capture_output, check, timeout):
        assert capture_output and check and 0 < timeout <= 8
        commands.append(command)
        if command[:4] == ["sudo", "-n", "kubectl", "--request-timeout=5s"]:
            assert tuple(command[4:]) in payloads
            data = json.dumps(payloads[tuple(command[4:])]).encode()
        else:
            assert command == [
                "sudo",
                "-n",
                "k3s",
                "ctr",
                "-n",
                "k8s.io",
                "content",
                "get",
                configured,
            ]
            data = index
        return subprocess.CompletedProcess(command, 0, stdout=data)

    def open_metrics(self, url, *, timeout):
        assert timeout == 3
        urls.append(url)
        return io.BytesIO(b"process_resident_memory_bytes 1024\n")

    def read_proc(path, *args, **kwargs):
        if str(path) == str(Path("/proc/meminfo")):
            return "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\n"
        assert str(path) == str(Path("/proc/stat"))
        return "cpu 1 2 3 4 5 6 7 8\n"

    monkeypatch.setattr(probe.subprocess, "run", run)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_metrics)
    monkeypatch.setattr(Path, "read_text", read_proc)
    monkeypatch.setattr(probe.shutil, "disk_usage", lambda _: SimpleNamespace(total=100, free=50))
    monkeypatch.setattr(probe.os, "cpu_count", lambda: 4)
    result = probe.collect_snapshot(ns)
    assert len(commands) == 7
    assert set(urls) == {
        "http://10.42.0.1:8000/metrics",
        "http://10.42.0.2:8081/metrics",
        "http://10.42.0.3:8082/metrics",
        "http://10.42.0.6:8000/metrics",
    }
    assert len(result["scrapes"]) == 4 and result["errors"] == []
    assert result["inventory"]["image_relationships"][0]["verified"] is True
    encoded = json.dumps(result)
    assert all(value not in encoded for value in ("10.42.0.", "private-pod", "secret-config"))
    with pytest.raises(ValueError, match="namespace_rejected"):
        probe.collect_snapshot("bad;namespace")
    assert len(commands) == 7


def test_interrupted_probe_is_distinct_from_unsubmitted_observations(tmp_path):
    from scripts.collect_business_telemetry import ObservationPlan, run_collection

    def interrupted(plan):
        raise KeyboardInterrupt

    result = run_collection(
        ObservationPlan(ssh_host="known-host", samples=2),
        tmp_path / "interrupted",
        probe=interrupted,
    )
    assert result["status"] == "interrupted"
    assert [r["status"] for r in result["records"]] == ["interrupted", "not_run"]
    assert result["summary"]["planned_samples"] == 2


@pytest.mark.parametrize(
    "updates",
    [
        {"ssh_host": "-oProxyCommand=bad"},
        {"ssh_host": "host;bad"},
        {"namespace": "ns;bad"},
        {"samples": True},
        {"samples": 242},
        {"interval_seconds": float("inf")},
        {"interval_seconds": 1},
        {"samples": 241, "interval_seconds": 60},
    ],
)
def test_observation_plan_rejects_unsafe_or_unbounded_execution(updates):
    from pydantic import ValidationError
    from scripts.collect_business_telemetry import ObservationPlan

    with pytest.raises(ValidationError):
        ObservationPlan.model_validate({"ssh_host": "known-host", **updates})


def test_insufficient_time_budget_preserves_denominator_without_starting_ssh(tmp_path):
    from scripts.collect_business_telemetry import ObservationPlan, run_collection

    def unexpected(plan):
        pytest.fail("insufficient time budget must not start a probe")

    result = run_collection(
        ObservationPlan(ssh_host="known-host", samples=2, max_run_seconds=10),
        tmp_path / "short",
        probe=unexpected,
    )
    assert result["summary"]["not_run"] == 2
    assert result["summary"]["planned_samples"] == 2


def test_counter_reset_and_unverified_queue_zero_do_not_become_healthy_capacity():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    first = normalize_snapshot(snapshot())
    last = normalize_snapshot(snapshot("2026-09-25T00:00:10+00:00"))
    for observed, count in ((first, 5), (last, 1)):
        observed["scrapes"][0]["metrics"]["dependencies"] = {
            "model": {"success": {"count": count, "sum": count * 2, "buckets": {}}}
        }
    result = summarize_observations(
        [{"status": "observed", "snapshot": s} for s in (first, last)], interval_seconds=5
    )
    assert "dependency_counter_reset" in result["issues"]
    assert "queue_and_redis_producer_freshness_unverified" in result["issues"]
    assert result["dependencies"]["model"]["observed_events"] is None
    assert result["production_capacity_approved"] is False


def test_business_phase_overlap_does_not_attest_target_identity():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    result = summarize_observations(
        [{"status": "observed", "snapshot": normalize_snapshot(snapshot())}],
        interval_seconds=5,
        business_report={
            "phases": [
                {
                    "phase": "ramp",
                    "repetition": 1,
                    "started_at": "2026-09-25T00:00:00+00:00",
                    "completed_at": "2026-09-25T00:01:00+00:00",
                }
            ]
        },
    )
    assert result["business_windows"][0]["observed_samples"] == 1
    assert result["business_windows"][0]["target_binding_verified"] is False


def resource_snapshot():
    from datetime import datetime

    from enterprise_doc_core.telemetry import MetricsRuntime

    raw = snapshot()
    runtime = MetricsRuntime.create()
    now = datetime.fromisoformat(raw["captured_at"]).timestamp()
    runtime.record_resource_observation("queue", 0, observed_at=now - 2)
    runtime.record_resource_observation("redis", 3, observed_at=now - 1)
    for scrape in raw["scrapes"]:
        if scrape["role"] == "worker":
            scrape["text"] = (
                "process_resident_memory_bytes 1024\nprocess_cpu_seconds_total 2\n"
                "process_start_time_seconds 1\n"
                + "\n".join(
                    line
                    for line in runtime.render().decode().splitlines()
                    if not line.startswith(("process_", "# HELP process_", "# TYPE process_"))
                )
                + "\n"
            )
    return raw


def test_fresh_worker_resource_observations_resolve_only_the_telemetry_gap():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    report = summarize_observations(
        [{"status": "observed", "snapshot": normalize_snapshot(resource_snapshot())}],
        interval_seconds=5,
    )
    assert report["status"] == "read_only_observed", report["issues"]
    assert report["unverified_gauges"] == []
    assert report["production_capacity_approved"] is False


@pytest.mark.parametrize(
    "fault", ["missing", "failed", "stale", "future", "restarted", "nonfinite"]
)
def test_resource_freshness_rejects_missing_failed_stale_and_wrong_process_samples(fault):
    from datetime import datetime

    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    sample = normalize_snapshot(resource_snapshot())
    worker = next(s for s in sample["scrapes"] if s["role"] == "worker")
    now = datetime.fromisoformat(sample["captured_at"]).timestamp()
    metadata = worker["metrics"]["resource_samples"]
    if fault == "missing":
        worker["metrics"].pop("resource_samples")
    elif fault == "failed":
        metadata["queue"]["success"] = 0
    elif fault == "stale":
        metadata["queue"]["last_success_timestamp_seconds"] = now - 46
    elif fault == "future":
        metadata["queue"]["last_success_timestamp_seconds"] = now + 6
    elif fault == "restarted":
        worker["metrics"]["gauges"]["process_start_time_seconds"] = now
    elif fault == "nonfinite":
        worker["metrics"]["gauges"]["enterprise_doc_queue_oldest_age_seconds"] = None
    result = summarize_observations(
        [{"status": "observed", "snapshot": sample}], interval_seconds=5
    )
    assert result["status"] == "observation_incomplete"
    assert "queue_and_redis_producer_freshness_unverified" in result["issues"]
    assert "enterprise_doc_queue_oldest_age_seconds" in result["unverified_gauges"]


def test_resource_freshness_does_not_use_api_samples_to_replace_a_missing_worker_producer():
    from scripts.business_capacity_telemetry import normalize_snapshot, summarize_observations

    sample = normalize_snapshot(resource_snapshot())
    api = next(s for s in sample["scrapes"] if s["role"] == "api")
    worker = next(s for s in sample["scrapes"] if s["role"] == "worker")
    api["metrics"], worker["metrics"] = worker["metrics"], api["metrics"]
    report = summarize_observations(
        [{"status": "observed", "snapshot": sample}], interval_seconds=5
    )
    assert "queue_and_redis_producer_freshness_unverified" in report["issues"]


def test_resource_metric_labels_and_duplicate_series_are_rejected():
    from scripts.business_capacity_telemetry import parse_metrics

    with pytest.raises(ValueError, match="metric_labels_rejected"):
        parse_metrics('enterprise_doc_resource_sample_success{source="private-tenant"} 1')
    with pytest.raises(ValueError, match="duplicate_metric"):
        parse_metrics('enterprise_doc_resource_sample_success{source="queue"} 1\n' * 2)
