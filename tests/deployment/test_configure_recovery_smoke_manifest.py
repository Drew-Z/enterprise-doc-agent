from __future__ import annotations

import copy
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "configure_recovery_smoke_manifest.py"
SPEC = spec_from_file_location("configure_recovery_smoke_manifest_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_recovery = module_from_spec(SPEC)
sys.modules[SPEC.name] = configure_recovery
SPEC.loader.exec_module(configure_recovery)

API_IMAGE = "ghcr.io/example/api@sha256:" + "a" * 64
WORKER_IMAGE = "ghcr.io/example/worker@sha256:" + "b" * 64
CONSUMER_IMAGE = "ghcr.io/example/consumer@sha256:" + "c" * 64


def _rendered_documents() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["kubectl", "kustomize", "infra/k8s/overlays/recovery-smoke"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(completed.stdout) if isinstance(item, dict)]


def _configure(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return configure_recovery.configure_recovery_smoke_manifest(
        documents,
        api_image=API_IMAGE,
        worker_image=WORKER_IMAGE,
        consumer_image=CONSUMER_IMAGE,
        database_egress_cidrs="52.74.252.201/32,52.77.146.31/32",
        object_store_endpoint="https://objects.example.com",
        documents_bucket="recovery-documents",
        artifacts_bucket="recovery-artifacts",
        model_base_url="https://model.example.com/v1",
        model_name="reviewed-model",
        embedding_base_url="https://embedding.example.com/v1",
        embedding_model_name="reviewed-embedding",
    )


def test_recovery_overlay_is_private_minimal_and_configurable() -> None:
    configured = _configure(_rendered_documents())
    kinds = {item["kind"] for item in configured}
    assert "Ingress" not in kinds
    assert "Job" not in kinds
    assert "Secret" not in kinds
    deployments = {item["metadata"]["name"] for item in configured if item["kind"] == "Deployment"}
    assert deployments == configure_recovery.DEPLOYMENT_NAMES
    services = [item for item in configured if item["kind"] == "Service"]
    assert {item["metadata"]["name"] for item in services} == configure_recovery.SERVICE_NAMES
    assert "enterprise-doc-consumer" in deployments
    assert "enterprise-doc-consumer" not in configure_recovery.SERVICE_NAMES
    deployment_images = {
        item["metadata"]["name"]: item["spec"]["template"]["spec"]["containers"][0]["image"]
        for item in configured
        if item["kind"] == "Deployment"
    }
    assert deployment_images["enterprise-doc-consumer"] == CONSUMER_IMAGE
    postgres_policy = next(
        item
        for item in configured
        if item["kind"] == "NetworkPolicy"
        and item["metadata"]["name"] == configure_recovery.POSTGRES_POLICY_NAME
    )
    selected_names = postgres_policy["spec"]["podSelector"]["matchExpressions"][0]["values"]
    assert "enterprise-doc-consumer" in selected_names
    redis_policy = next(
        item
        for item in configured
        if item["kind"] == "NetworkPolicy"
        and item["metadata"]["name"] == "enterprise-doc-redis-ingress"
    )
    redis_sources = redis_policy["spec"]["ingress"][0]["from"]
    assert any(
        source.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name")
        == "enterprise-doc-consumer"
        for source in redis_sources
    )
    assert all(item.get("spec", {}).get("type") in {None, "ClusterIP"} for item in services)
    config = next(
        item
        for item in configured
        if item["kind"] == "ConfigMap"
        and item["metadata"]["name"] == configure_recovery.CONFIG_NAME
    )
    assert config["data"]["OBJECT_STORE__DOCUMENTS_BUCKET"] == "recovery-documents"
    assert config["data"]["OBJECT_STORE__ARTIFACTS_BUCKET"] == "recovery-artifacts"
    assert "DATABASE__URL" not in config["data"]


def test_recovery_configuration_rejects_public_or_mutable_inputs() -> None:
    documents = _rendered_documents()
    with_ingress = copy.deepcopy(documents)
    with_ingress.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "forbidden",
                "namespace": configure_recovery.NAMESPACE_NAME,
            },
        }
    )
    with pytest.raises(ValueError, match="forbidden kinds"):
        _configure(with_ingress)
    with pytest.raises(ValueError, match="immutable sha256"):
        configure_recovery.configure_recovery_smoke_manifest(
            documents,
            api_image="ghcr.io/example/api:latest",
            worker_image=WORKER_IMAGE,
            consumer_image=CONSUMER_IMAGE,
            database_egress_cidrs="52.74.252.201/32",
            object_store_endpoint="https://objects.example.com",
            documents_bucket="recovery-documents",
            artifacts_bucket="recovery-artifacts",
            model_base_url="https://model.example.com/v1",
            model_name="reviewed-model",
            embedding_base_url="https://embedding.example.com/v1",
            embedding_model_name="reviewed-embedding",
        )


def test_recovery_configuration_rejects_non_global_database_cidr() -> None:
    with pytest.raises(ValueError, match="global unicast"):
        configure_recovery.configure_recovery_smoke_manifest(
            _rendered_documents(),
            api_image=API_IMAGE,
            worker_image=WORKER_IMAGE,
            consumer_image=CONSUMER_IMAGE,
            database_egress_cidrs="127.0.0.1/32",
            object_store_endpoint="https://objects.example.com",
            documents_bucket="recovery-documents",
            artifacts_bucket="recovery-artifacts",
            model_base_url="https://model.example.com/v1",
            model_name="reviewed-model",
            embedding_base_url="https://embedding.example.com/v1",
            embedding_model_name="reviewed-embedding",
        )
