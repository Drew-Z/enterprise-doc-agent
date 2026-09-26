"""Sanitized, read-only observations; never a production capacity approval."""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from prometheus_client.parser import text_string_to_metric_families

from scripts.capacity_host_probe import PORTS, ROLES, digest

PROCESS_GAUGES = (
    "process_cpu_seconds_total",
    "process_resident_memory_bytes",
    "process_start_time_seconds",
    "enterprise_doc_database_pool_utilization_percent",
)
GAUGES = (
    *PROCESS_GAUGES,
    "enterprise_doc_redis_connected_clients",
    "enterprise_doc_queue_oldest_age_seconds",
    "enterprise_doc_redis_connections",
)
RESOURCE_GAUGES = {
    "queue": "enterprise_doc_queue_oldest_age_seconds",
    "redis": "enterprise_doc_redis_connected_clients",
}
RESOURCE_FIELDS = {
    "enterprise_doc_resource_sample_success": "success",
    "enterprise_doc_resource_last_success_timestamp_seconds": "last_success_timestamp_seconds",
}
DEPENDENCY_PREFIX = "enterprise_doc_dependency_duration_seconds_"


def parse_metrics(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > 262144:
        raise ValueError("metrics_too_large")
    try:
        families = list(text_string_to_metric_families(text))
    except ValueError:
        raise ValueError("invalid_metrics") from None
    gauges: dict[str, float | None] = dict.fromkeys(GAUGES)
    dependencies: dict[str, Any] = {}
    resources: dict[str, dict[str, float | None]] = {
        source: dict.fromkeys(RESOURCE_FIELDS.values()) for source in RESOURCE_GAUGES
    }
    nonfinite = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for family in families:
        for sample in family.samples:
            suffix = sample.name.removeprefix(DEPENDENCY_PREFIX)
            if sample.name not in (*GAUGES, *RESOURCE_FIELDS) and (
                not sample.name.startswith(DEPENDENCY_PREFIX)
                or suffix not in {"count", "sum", "bucket"}
            ):
                continue
            labels = sample.labels
            if sample.name in GAUGES:
                if labels:
                    raise ValueError("metric_labels_rejected")
            elif sample.name in RESOURCE_FIELDS:
                if set(labels) != {"source"} or labels["source"] not in RESOURCE_GAUGES:
                    raise ValueError("metric_labels_rejected")
            elif (
                set(labels)
                != (
                    {"dependency", "result", "le"}
                    if suffix == "bucket"
                    else {"dependency", "result"}
                )
                or labels["dependency"] not in {"database", "redis", "object_store", "model"}
                or labels["result"] not in {"success", "error"}
            ):
                raise ValueError("metric_labels_rejected")
            if suffix == "bucket":
                try:
                    bound = float(labels["le"])
                except ValueError:
                    raise ValueError("invalid_bucket") from None
                if math.isnan(bound) or bound < 0 or len(labels["le"]) > 20:
                    raise ValueError("invalid_bucket")
            identity = (sample.name, tuple(sorted(labels.items())))
            if identity in seen:
                raise ValueError("duplicate_metric")
            seen.add(identity)
            value = float(sample.value)
            observed = value if math.isfinite(value) and value >= 0 else None
            if observed is None:
                nonfinite.append(sample.name)
            if sample.name in GAUGES:
                gauges[sample.name] = observed
            elif sample.name in RESOURCE_FIELDS:
                resources[labels["source"]][RESOURCE_FIELDS[sample.name]] = observed
            else:
                series = dependencies.setdefault(labels["dependency"], {}).setdefault(
                    labels["result"], {"count": None, "sum": None, "buckets": {}}
                )
                if suffix == "bucket":
                    series["buckets"][labels["le"]] = observed
                else:
                    series[suffix] = observed
    return {
        "gauges": gauges,
        "dependencies": dependencies,
        "resource_samples": resources,
        "nonfinite": sorted(set(nonfinite)),
    }


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed


def quantity(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(n|u|m|Ki|Mi|Gi|Ti|K|M|G|T)?", value)
    if match is None:
        raise ValueError("invalid_resource_quantity")
    scale = {
        "": 1,
        "n": 1e-9,
        "u": 1e-6,
        "m": 1e-3,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    number = float(Decimal(match.group(1))) * scale[match.group(2) or ""]
    if not math.isfinite(number):
        raise ValueError("invalid_resource_quantity")
    return number


def normalize_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("invalid_probe_version")
    result = copy.deepcopy(raw)
    timestamp(result["captured_at"])
    if timestamp(result["collection_started_at"]) > timestamp(result["captured_at"]):
        raise ValueError("invalid_probe_window")
    for item in result["scrapes"]:
        text = item.pop("text")
        item["metrics"] = None
        item["error_code"] = "metrics_unavailable" if item.get("error_code") else None
        if text is not None:
            try:
                item["metrics"] = parse_metrics(text)
            except (ValueError, TypeError):
                item["error_code"] = "metrics_rejected"
    return result


def identity(snapshot: dict[str, Any]) -> str:
    inventory = snapshot["inventory"]
    return digest(
        {
            "namespace": snapshot["namespace_sha256"],
            "cluster": inventory["cluster_sha256"],
            "configuration": inventory["configuration_sha256"],
            "nodes": inventory["nodes"],
            "deployments": [
                {
                    key: v[key]
                    for key in (
                        "role",
                        "uid_sha256",
                        "template_sha256",
                        "containers",
                        "desired_replicas",
                    )
                }
                for v in inventory["deployments"]
            ],
            "image_relationships": inventory.get("image_relationships", []),
            "pods": [
                {"role": p["role"], "uid": p["uid_sha256"], "containers": p["containers"]}
                for p in inventory["pods"]
            ],
        }
    )


def _dependency_delta(samples: list[dict[str, Any]], issues: set[str]) -> dict[str, Any]:
    result = {}
    for dependency in ("object_store", "model"):
        calls, seconds, pairs = 0.0, 0.0, 0
        failed = False
        if len(samples) >= 2:
            before = {
                s["uid_sha256"]: s["metrics"] for s in samples[0]["scrapes"] if s.get("metrics")
            }
            after = {
                s["uid_sha256"]: s["metrics"] for s in samples[-1]["scrapes"] if s.get("metrics")
            }
            for uid in before.keys() & after.keys():
                first, last = before[uid], after[uid]
                for outcome in ("success", "error"):
                    a = first["dependencies"].get(dependency, {}).get(outcome)
                    b = last["dependencies"].get(dependency, {}).get(outcome)
                    if (
                        a is None
                        or b is None
                        or any(v.get(k) is None for v in (a, b) for k in ("count", "sum"))
                    ):
                        continue
                    if (
                        b["count"] < a["count"]
                        or b["sum"] < a["sum"]
                        or first["gauges"]["process_start_time_seconds"]
                        != last["gauges"]["process_start_time_seconds"]
                    ):
                        issues.add("dependency_counter_reset")
                        failed = True
                        continue
                    calls += b["count"] - a["count"]
                    seconds += b["sum"] - a["sum"]
                    pairs += 1
        result[dependency] = {
            "observed_events": calls if pairs and not failed else None,
            "mean_seconds": seconds / calls if calls and not failed else None,
            "observed_series_pairs": pairs,
            "p95_seconds": None,
        }
    return result


def _unverified_resources(sample: dict[str, Any], issues: set[str]) -> set[str]:
    expected = {p["uid_sha256"] for p in sample["inventory"]["pods"] if p["role"] == "worker"}
    scrapes = [s for s in sample["scrapes"] if s["role"] == "worker"]
    if (
        not expected
        or {s["uid_sha256"] for s in scrapes} != expected
        or len(scrapes) != len(expected)
    ):
        issues.add("resource_producer_missing")
        return set(RESOURCE_GAUGES.values())
    unverified = set()
    now = timestamp(sample["captured_at"]).timestamp()
    for scrape in scrapes:
        metrics = scrape.get("metrics")
        for source, gauge in RESOURCE_GAUGES.items():
            reason = None
            if metrics is None or scrape["error_code"]:
                reason = "unavailable"
            else:
                observed = metrics.get("resource_samples", {}).get(source, {})
                last = observed.get("last_success_timestamp_seconds")
                success = observed.get("success")
                started = metrics["gauges"].get("process_start_time_seconds")
                if last is None or success is None:
                    reason = "missing"
                elif success != 1:
                    reason = "failed"
                elif metrics["gauges"].get(gauge) is None:
                    reason = "nonfinite"
                elif (
                    last <= 0
                    or now - last > 45
                    or last - now > 5
                    or started is None
                    or last < started
                ):
                    reason = "stale"
            if reason is not None:
                issues.add(source + "_resource_observation_" + reason)
                unverified.add(gauge)
    return unverified


def summarize_observations(
    records: list[dict[str, Any]],
    *,
    interval_seconds: float,
    business_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    samples = [r["snapshot"] for r in records if r["status"] == "observed"]
    issues: set[str] = set()
    unverified = set(RESOURCE_GAUGES.values()) if not samples else set()
    if len(samples) != len(records) or not samples:
        issues.add("planned_samples_missing")
    identities = {identity(s) for s in samples}
    if len(identities) > 1:
        issues.add("deployment_identity_drift")
    unique_nodes: set[tuple[str, str]] = set()
    available_memory, disk, cpu, node_cpu = [], [], [], []
    latest: dict[str, Any] | None = None
    for sample in samples:
        unverified.update(_unverified_resources(sample, issues))
        captured = timestamp(sample["captured_at"])
        inventory = sample["inventory"]
        verified_pairs = {
            (p["configured_digest"], p["running_digest"])
            for p in inventory.get("image_relationships", [])
            if p["verified"]
        }
        if sample["errors"]:
            issues.add("probe_source_unavailable")
        if inventory["configuration_sha256"] is None or inventory["cluster_sha256"] is None:
            issues.add("deployment_identity_missing")
        if len(inventory["nodes"]) != 1:
            issues.add("single_node_required")
        if {d["role"] for d in inventory["deployments"]} != set(ROLES):
            issues.add("deployment_missing")
        for deployment in inventory["deployments"]:
            if (
                deployment["desired_replicas"] < 1
                or deployment["ready_replicas"] != deployment["desired_replicas"]
                or deployment["updated_replicas"] != deployment["desired_replicas"]
                or deployment["generation"] != deployment["observed_generation"]
            ):
                issues.add("deployment_not_ready")
            if (
                sum(p["role"] == deployment["role"] for p in inventory["pods"])
                != deployment["desired_replicas"]
            ):
                issues.add("pod_replica_mismatch")
        expected_scrapes = {p["uid_sha256"] for p in inventory["pods"] if p["role"] in PORTS}
        if {s["uid_sha256"] for s in sample["scrapes"]} != expected_scrapes:
            issues.add("metrics_target_missing")
        for pod in inventory["pods"]:
            if pod["phase"] != "Running" or not pod["containers"]:
                issues.add("pod_not_ready")
            for container in pod["containers"]:
                if not container["ready"]:
                    issues.add("pod_not_ready")
                if container["last_oom"]:
                    issues.add("pod_oom_observed")
                if not container["running_digest"] or (
                    container["configured_digest"] != container["running_digest"]
                    and (container["configured_digest"], container["running_digest"])
                    not in verified_pairs
                ):
                    issues.add("image_identity_missing_or_mismatched")
        for kind, expected in (("node", inventory["nodes"]), ("pod", inventory["pods"])):
            metrics = sample[kind + "_metrics"]
            if {m["uid_sha256"] for m in metrics} != {m["uid_sha256"] for m in expected}:
                issues.add(kind + "_metrics_missing")
            for metric in metrics:
                age = (captured - timestamp(metric["timestamp"])).total_seconds()
                if age < -5 or age > 45:
                    issues.add("stale_" + kind + "_metrics")
                if kind == "node":
                    unique_nodes.add((metric["uid_sha256"], metric["timestamp"]))
                    node_cpu.append(quantity(metric["usage"]["cpu"]))
        for scrape in sample["scrapes"]:
            metrics = scrape["metrics"]
            if scrape["error_code"] or metrics is None:
                issues.add("application_metrics_unavailable")
            elif any(metrics["gauges"].get(name) is None for name in PROCESS_GAUGES):
                issues.add("application_metric_missing_or_nonfinite")
        host = sample["host"]
        if host is None:
            issues.add("host_resources_missing")
        else:
            available_memory.append(host["memory_available_bytes"])
            disk.append(host["root_disk_available_bytes"])
            if host["cpu_count"] != 4:
                issues.add("four_cpu_profile_mismatch")
            if latest is not None and latest["host"] is not None:
                total = host["cpu_total_ticks"] - latest["host"]["cpu_total_ticks"]
                idle = host["cpu_idle_ticks"] - latest["host"]["cpu_idle_ticks"]
                if total > 0 and 0 <= idle <= total:
                    cpu.append(100 * (total - idle) / total)
                else:
                    issues.add("node_counter_reset_or_no_interval")
        if latest is not None:
            gap = (captured - timestamp(latest["captured_at"])).total_seconds()
            if gap <= 0 or gap > interval_seconds * 2.5:
                issues.add("observation_gap")
        latest = sample
    if unverified:
        issues.add("queue_and_redis_producer_freshness_unverified")
    dependencies = _dependency_delta(samples, issues)
    windows = []
    if business_report is not None:
        for phase in business_report.get("phases", []):
            start, end = phase.get("started_at"), phase.get("completed_at")
            times = [
                timestamp(s["captured_at"])
                for s in samples
                if start
                and end
                and timestamp(start) <= timestamp(s["captured_at"]) <= timestamp(end)
            ]
            windows.append(
                {
                    "phase": phase["phase"],
                    "repetition": phase["repetition"],
                    "observed_samples": len(times),
                    "target_binding_verified": False,
                    "covered_seconds": (max(times) - min(times)).total_seconds()
                    if len(times) > 1
                    else 0,
                }
            )
    return {
        "scope": "read-only-business-resource-observation",
        "schema_version": 1,
        "status": "observation_incomplete" if issues else "read_only_observed",
        "production_capacity_approved": False,
        "planned_samples": len(records),
        "observed_samples": len(samples),
        "not_run": sum(r["status"] == "not_run" for r in records),
        "issues": sorted(issues),
        "identity_sha256": next(iter(identities)) if len(identities) == 1 else None,
        "unique_node_metric_samples": len(unique_nodes),
        "host": {
            "memory_available_bytes_min": min(available_memory) if available_memory else None,
            "root_disk_available_bytes_min": min(disk) if disk else None,
            "cpu_busy_percent_max": max(cpu) if cpu else None,
            "kubernetes_cpu_cores_max": max(node_cpu) if node_cpu else None,
        },
        "dependencies": dependencies,
        "unverified_gauges": sorted(unverified),
        "resource_observation_scope": "Worker due durable jobs and shared Redis server clients",
        "business_windows": windows,
        "limitations": [
            "No workload, capacity target or production approval is implied.",
            "Kubernetes resource timestamps may repeat; unique samples are counted separately.",
            "Dependency values are process-observed counters; "
            "absent/unchanged work has no latency estimate.",
            "Dependency events are not provider invoices or attributed business/model requests.",
            "Queue/Redis values require a successful Worker sample within 45 seconds; "
            "legacy gauges and zeros alone are not idle proof.",
            "Business windows are temporal overlap only; actual target binding remains unverified.",
            "No browser recovery, migration version, source commit or secret-version attestation.",
        ],
    }
