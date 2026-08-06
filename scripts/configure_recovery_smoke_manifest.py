from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

NAMESPACE_NAME = "enterprise-doc-agent-recovery"
CONFIG_NAME = "enterprise-doc-config"
POSTGRES_POLICY_NAME = "enterprise-doc-recovery-postgres-egress"
DEPLOYMENT_NAMES = {
    "enterprise-doc-api",
    "enterprise-doc-worker",
    "enterprise-doc-consumer",
    "enterprise-doc-redis",
}
SERVICE_NAMES = {
    "enterprise-doc-api",
    "enterprise-doc-worker",
    "enterprise-doc-redis",
}
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
IMMUTABLE_IMAGE = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+"
    r"@sha256:[0-9a-f]{64}$"
)


def _single_document(documents: list[dict[str, Any]], *, kind: str, name: str) -> dict[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and isinstance(document.get("metadata"), dict)
        and document["metadata"].get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"rendered manifests must contain exactly one {kind}/{name}")
    return matches[0]


def _dns_label(value: str, *, description: str) -> str:
    normalized = value.strip()
    if len(normalized) > 63 or not DNS_LABEL.fullmatch(normalized):
        raise ValueError(f"{description} must be a DNS label of 63 characters or fewer")
    return normalized


def _https_origin(value: str, *, description: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{description} must be an exact HTTPS origin")
    if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
        raise ValueError(f"{description} must not target localhost")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError(f"{description} must target a global unicast address")
    return normalized


def _model_base_url(value: str, *, description: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.rstrip("/").endswith("/v1")
    ):
        raise ValueError(f"{description} must be an OpenAI-compatible HTTPS /v1 URL")
    return normalized


def _model_name(value: str, *, description: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{description} must contain 1-200 printable characters")
    return normalized


def _immutable_image(value: str, *, description: str) -> str:
    normalized = value.strip()
    if not IMMUTABLE_IMAGE.fullmatch(normalized):
        raise ValueError(f"{description} must use an immutable sha256 digest")
    return normalized


def _host_cidrs(value: str) -> list[str]:
    candidates = [item.strip() for item in value.split(",") if item.strip()]
    if not candidates:
        raise ValueError("database egress CIDRs must not be empty")
    result: set[str] = set()
    for candidate in candidates:
        try:
            network = ipaddress.ip_network(candidate, strict=True)
        except ValueError as error:
            raise ValueError("database egress CIDRs must contain valid host CIDRs") from error
        if network.prefixlen != network.max_prefixlen or not network.network_address.is_global:
            raise ValueError("database egress CIDRs must contain global unicast host addresses")
        result.add(str(network))
    return sorted(result, key=lambda item: (ipaddress.ip_network(item).version, item))


def _load_documents(path: Path) -> list[dict[str, Any]]:
    documents = [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]
    if not documents or not all(isinstance(item, dict) for item in documents):
        raise ValueError("source must contain Kubernetes YAML objects")
    return documents


def _assert_isolated(documents: list[dict[str, Any]]) -> None:
    forbidden_kinds = {"Ingress", "Job", "Secret", "PersistentVolumeClaim"}
    found_forbidden = sorted(
        str(document.get("kind"))
        for document in documents
        if document.get("kind") in forbidden_kinds
    )
    if found_forbidden:
        raise ValueError(f"recovery manifests contain forbidden kinds: {found_forbidden}")
    deployments = {
        str(document["metadata"]["name"])
        for document in documents
        if document.get("kind") == "Deployment"
    }
    if deployments != DEPLOYMENT_NAMES:
        raise ValueError(
            "recovery manifests must contain only API, worker, consumer, and Redis deployments"
        )
    services = {
        str(document["metadata"]["name"])
        for document in documents
        if document.get("kind") == "Service"
    }
    if services != SERVICE_NAMES:
        raise ValueError("recovery manifests must contain only API, worker, and Redis services")
    for document in documents:
        metadata = document.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("every recovery manifest must have metadata")
        if document.get("kind") == "Namespace":
            if metadata.get("name") != NAMESPACE_NAME:
                raise ValueError("recovery Namespace name is fixed")
        elif metadata.get("namespace") != NAMESPACE_NAME:
            raise ValueError("every namespaced recovery object must use the recovery Namespace")
        if document.get("kind") == "Service" and document.get("spec", {}).get("type") not in {
            None,
            "ClusterIP",
        }:
            raise ValueError("recovery Services must remain ClusterIP-only")


def configure_recovery_smoke_manifest(
    documents: list[dict[str, Any]],
    *,
    api_image: str,
    worker_image: str,
    consumer_image: str,
    database_egress_cidrs: str,
    object_store_endpoint: str,
    documents_bucket: str,
    artifacts_bucket: str,
    model_base_url: str,
    model_name: str,
    embedding_base_url: str,
    embedding_model_name: str,
) -> list[dict[str, Any]]:
    configured = copy.deepcopy(documents)
    _assert_isolated(configured)
    config = _single_document(configured, kind="ConfigMap", name=CONFIG_NAME)
    data = config.get("data")
    if not isinstance(data, dict):
        raise ValueError("recovery ConfigMap must contain data")
    endpoint = _https_origin(object_store_endpoint, description="object-store endpoint")
    data.update(
        {
            "OBJECT_STORE__ENDPOINT": endpoint,
            "OBJECT_STORE__PRESIGN_ENDPOINT": endpoint,
            "OBJECT_STORE__DOCUMENTS_BUCKET": _dns_label(
                documents_bucket, description="documents bucket"
            ),
            "OBJECT_STORE__ARTIFACTS_BUCKET": _dns_label(
                artifacts_bucket, description="artifacts bucket"
            ),
            "MODEL__BASE_URL": _model_base_url(model_base_url, description="model base URL"),
            "MODEL__MODEL_NAME": _model_name(model_name, description="model name"),
            "EMBEDDING__BASE_URL": _model_base_url(
                embedding_base_url, description="embedding base URL"
            ),
            "EMBEDDING__MODEL_NAME": _model_name(
                embedding_model_name, description="embedding model name"
            ),
        }
    )
    images = {
        "enterprise-doc-api": _immutable_image(api_image, description="API image"),
        "enterprise-doc-worker": _immutable_image(worker_image, description="worker image"),
        "enterprise-doc-consumer": _immutable_image(consumer_image, description="consumer image"),
    }
    for deployment_name, image in images.items():
        deployment = _single_document(configured, kind="Deployment", name=deployment_name)
        containers = deployment["spec"]["template"]["spec"].get("containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError(f"Deployment/{deployment_name} must contain exactly one container")
        containers[0]["image"] = image
    policy = _single_document(
        configured,
        kind="NetworkPolicy",
        name=POSTGRES_POLICY_NAME,
    )
    cidrs = _host_cidrs(database_egress_cidrs)
    policy["spec"]["egress"][0]["to"] = [{"ipBlock": {"cidr": cidr}} for cidr in cidrs]
    rendered = yaml.safe_dump_all(configured, sort_keys=False)
    if "example.invalid" in rendered or "replace-with-" in rendered:
        raise ValueError("configured recovery manifest still contains placeholders")
    return configured


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure an isolated, no-Ingress recovery smoke manifest"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--consumer-image", required=True)
    parser.add_argument("--database-egress-cidrs", required=True)
    parser.add_argument("--object-store-endpoint", required=True)
    parser.add_argument("--documents-bucket", required=True)
    parser.add_argument("--artifacts-bucket", required=True)
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--embedding-base-url", required=True)
    parser.add_argument("--embedding-model-name", required=True)
    args = parser.parse_args()
    try:
        documents = configure_recovery_smoke_manifest(
            _load_documents(args.source),
            api_image=args.api_image,
            worker_image=args.worker_image,
            consumer_image=args.consumer_image,
            database_egress_cidrs=args.database_egress_cidrs,
            object_store_endpoint=args.object_store_endpoint,
            documents_bucket=args.documents_bucket,
            artifacts_bucket=args.artifacts_bucket,
            model_base_url=args.model_base_url,
            model_name=args.model_name,
            embedding_base_url=args.embedding_base_url,
            embedding_model_name=args.embedding_model_name,
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        parser.error(str(error))
    rendered = yaml.safe_dump_all(documents, sort_keys=False)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(rendered, encoding="utf-8")
    args.destination.chmod(0o600)
    print(
        json.dumps(
            {
                "status": "configured",
                "namespace": NAMESPACE_NAME,
                "resource_count": len(documents),
                "manifest_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "public_ingress_count": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
