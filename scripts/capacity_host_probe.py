"""Read-only Linux probe, sent over SSH stdin; uses only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROLES = ("api", "worker", "consumer", "web", "redis")
PORTS = {"api": 8000, "worker": 8081, "consumer": 8082}
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def image_digest(value: str) -> str | None:
    match = re.search(r"(?:^|@)(sha256:[0-9a-f]{64})$", value)
    return match.group(1) if match else None


def role_of(name: str) -> str | None:
    role = name.removeprefix("enterprise-doc-")
    return role if name.startswith("enterprise-doc-") and role in ROLES else None


def runtime_content(reference: str) -> bytes:
    if image_digest(reference) != reference:
        raise ValueError("digest_rejected")
    result = subprocess.run(
        ["sudo", "-n", "k3s", "ctr", "-n", "k8s.io", "content", "get", reference],
        capture_output=True,
        timeout=4,
        check=True,
    )
    if len(result.stdout) > 65536:
        raise ValueError("index_too_large")
    return result.stdout


def verify_image_relationship(
    configured: str,
    running: str,
    architecture: str,
    *,
    read_content: Callable[[str], bytes] = runtime_content,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured_digest": configured,
        "running_digest": running,
        "architecture": architecture,
        "verified": False,
        "kind": "unresolved",
    }
    if image_digest(configured) != configured or image_digest(running) != running:
        return result
    if configured == running:
        return {**result, "verified": True, "kind": "same_digest"}

    def read_index(reference: str) -> dict[str, Any]:
        raw = read_content(reference)
        if len(raw) > 65536 or "sha256:" + hashlib.sha256(raw).hexdigest() != reference:
            raise ValueError("index_hash_mismatch")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("schemaVersion") != 2
            or not isinstance(payload.get("manifests"), list)
            or not all(isinstance(item, dict) for item in payload["manifests"])
        ):
            raise ValueError("invalid_index")
        return payload

    try:
        payload = read_index(configured)
        if payload.get("mediaType") not in INDEX_TYPES:
            return result
        selected = [
            item
            for item in payload["manifests"]
            if isinstance(item.get("platform"), dict)
            and item.get("platform", {}).get("architecture") == architecture
            and item.get("platform", {}).get("os") == "linux"
            and item.get("mediaType") in MANIFEST_TYPES
        ]
        if len(selected) != 1:
            return result
        child = selected[0].get("digest")
        if not isinstance(child, str) or image_digest(child) != child:
            return result
        if child == running:
            return {
                **result,
                "verified": True,
                "kind": "verified_platform_manifest",
                "platform_manifest_digest": child,
            }
        archive = read_index(running)
        if archive.get("mediaType") is not None and archive["mediaType"] not in INDEX_TYPES:
            return result
        for reference, media_type in (
            (configured, payload["mediaType"]),
            (child, selected[0]["mediaType"]),
        ):
            if (
                sum(
                    item.get("digest") == reference and item.get("mediaType") == media_type
                    for item in archive["manifests"]
                )
                != 1
            ):
                return result
        return {
            **result,
            "verified": True,
            "kind": "verified_runtime_archive_index",
            "platform_manifest_digest": child,
        }
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    return result


def project_inventory(
    deployments: list[dict[str, Any]],
    pods: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    workloads = []
    for item in deployments:
        role = role_of(item["metadata"]["name"])
        if role is None:
            continue
        template = item["spec"]["template"]
        status = item.get("status", {})
        workloads.append(
            {
                "role": role,
                "uid_sha256": digest(item["metadata"]["uid"]),
                "template_sha256": digest(template),
                "generation": item["metadata"].get("generation"),
                "observed_generation": status.get("observedGeneration"),
                "desired_replicas": item["spec"].get("replicas", 1),
                "ready_replicas": status.get("readyReplicas", 0),
                "updated_replicas": status.get("updatedReplicas", 0),
                "containers": [
                    {
                        "name": c["name"],
                        "configured_digest": image_digest(c["image"]),
                        "limits": c.get("resources", {}).get("limits", {}),
                    }
                    for c in template["spec"]["containers"]
                ],
            }
        )
    running = []
    for item in pods:
        role = role_of(item["metadata"].get("labels", {}).get("app.kubernetes.io/name", ""))
        if role is None:
            continue
        states = {s["name"]: s for s in item.get("status", {}).get("containerStatuses", [])}
        containers = []
        for c in item["spec"]["containers"]:
            state = states.get(c["name"], {})
            containers.append(
                {
                    "name": c["name"],
                    "configured_digest": image_digest(c["image"]),
                    "running_digest": image_digest(state.get("imageID", "")),
                    "ready": state.get("ready", False),
                    "restarts": state.get("restartCount"),
                    "last_oom": state.get("lastState", {}).get("terminated", {}).get("reason")
                    == "OOMKilled",
                }
            )
        running.append(
            {
                "role": role,
                "uid_sha256": digest(item["metadata"]["uid"]),
                "phase": item.get("status", {}).get("phase", "Unknown"),
                "containers": containers,
            }
        )
    return {
        "cluster_sha256": digest(sorted(n["metadata"]["uid"] for n in nodes)) if nodes else None,
        "configuration_sha256": digest({k: config.get(k, {}) for k in ("data", "binaryData")})
        if config is not None
        else None,
        "nodes": [
            {
                "uid_sha256": digest(n["metadata"]["uid"]),
                "cpu_capacity": n["status"]["capacity"].get("cpu"),
                "memory_capacity": n["status"]["capacity"].get("memory"),
            }
            for n in nodes
        ],
        "deployments": sorted(workloads, key=lambda d: d["role"]),
        "pods": sorted(running, key=lambda p: p["uid_sha256"]),
    }


def kubectl(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["sudo", "-n", "kubectl", "--request-timeout=5s", *args],
        capture_output=True,
        check=True,
        timeout=8,
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("kubernetes_response_too_large")
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def read_metrics(address: str, port: int) -> str:
    parsed = ipaddress.ip_address(address)
    if not parsed.is_private or parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast:
        raise ValueError("pod_address_rejected")
    host = f"[{address}]" if parsed.version == 6 else address
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(f"http://{host}:{port}/metrics", timeout=3) as response:
        content = bytes(response.read(262145))
    if len(content) > 262144:
        raise ValueError("metrics_too_large")
    return content.decode("utf-8")


def host_resources() -> dict[str, Any]:
    memory = {
        line.split(":", 1)[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemTotal:", "MemAvailable:"))
    }
    ticks = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    disk = shutil.disk_usage("/")
    return {
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": memory["MemTotal"],
        "memory_available_bytes": memory["MemAvailable"],
        "cpu_total_ticks": sum(ticks),
        "cpu_idle_ticks": ticks[3] + ticks[4],
        "root_disk_total_bytes": disk.total,
        "root_disk_available_bytes": disk.free,
    }


def collect_snapshot(namespace: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", namespace):
        raise ValueError("namespace_rejected")
    started = datetime.now(UTC).isoformat()
    errors = []

    def read(name: str, args: list[str]) -> dict[str, Any]:
        try:
            return kubectl(args)
        except (OSError, ValueError, subprocess.SubprocessError, KeyError):
            errors.append(name + "_unavailable")
            return {}

    nodes = read("nodes", ["get", "nodes", "-o", "json"]).get("items", [])
    deployments = read("deployments", ["-n", namespace, "get", "deployments", "-o", "json"]).get(
        "items", []
    )
    pods = read("pods", ["-n", namespace, "get", "pods", "-o", "json"]).get("items", [])
    pods = [
        p
        for p in pods
        if role_of(p["metadata"].get("labels", {}).get("app.kubernetes.io/name", "")) is not None
    ]
    if len(pods) > 12 or len(nodes) > 16 or len(deployments) > 50:
        raise ValueError("inventory_budget_exceeded")
    config = read(
        "config", ["-n", namespace, "get", "configmap", "enterprise-doc-config", "-o", "json"]
    )
    inventory = project_inventory(deployments, pods, nodes, config or None)
    architecture = (
        nodes[0].get("status", {}).get("nodeInfo", {}).get("architecture", "unknown")
        if len(nodes) == 1
        else "unknown"
    )
    pairs = {
        (c["configured_digest"], c["running_digest"])
        for p in inventory["pods"]
        for c in p["containers"]
        if c["configured_digest"] and c["running_digest"]
    }
    inventory["image_relationships"] = [
        verify_image_relationship(a, b, architecture) for a, b in sorted(pairs)
    ]
    raw_node_metrics = read(
        "node_metrics", ["get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"]
    ).get("items", [])
    raw_pod_metrics = read(
        "pod_metrics", ["get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods"]
    ).get("items", [])
    node_ids = {n["metadata"]["name"]: digest(n["metadata"]["uid"]) for n in nodes}
    pod_ids = {p["metadata"]["name"]: digest(p["metadata"]["uid"]) for p in pods}
    node_metrics = [
        {
            "uid_sha256": node_ids[v["metadata"]["name"]],
            "timestamp": v["timestamp"],
            "window": v["window"],
            "usage": v["usage"],
        }
        for v in raw_node_metrics
        if v["metadata"]["name"] in node_ids
    ]
    pod_metrics = [
        {
            "uid_sha256": pod_ids[v["metadata"]["name"]],
            "timestamp": v["timestamp"],
            "window": v["window"],
            "containers": [{"name": c["name"], "usage": c["usage"]} for c in v["containers"]],
        }
        for v in raw_pod_metrics
        if v["metadata"]["name"] in pod_ids
    ]
    scrapes = []
    for pod in pods:
        role = role_of(pod["metadata"].get("labels", {}).get("app.kubernetes.io/name", ""))
        if role not in PORTS:
            continue
        item = {
            "role": role,
            "uid_sha256": digest(pod["metadata"]["uid"]),
            "scraped_at": datetime.now(UTC).isoformat(),
            "text": None,
            "error_code": None,
        }
        try:
            item["text"] = read_metrics(pod.get("status", {}).get("podIP", ""), PORTS[role])
        except (OSError, ValueError):
            item["error_code"] = "metrics_unavailable"
        scrapes.append(item)
    try:
        host = host_resources()
    except (OSError, ValueError, KeyError):
        host = None
        errors.append("host_resources_unavailable")
    return {
        "schema_version": 1,
        "collection_started_at": started,
        "captured_at": datetime.now(UTC).isoformat(),
        "namespace_sha256": digest(namespace),
        "inventory": inventory,
        "host": host,
        "node_metrics": node_metrics,
        "pod_metrics": pod_metrics,
        "scrapes": scrapes,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()
    deadline = threading.Timer(20, lambda: os._exit(124))
    deadline.daemon = True
    deadline.start()
    try:
        result = collect_snapshot(args.namespace)
    except Exception:
        print(json.dumps({"error_code": "probe_failed"}))
        raise SystemExit(1) from None
    finally:
        deadline.cancel()
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
