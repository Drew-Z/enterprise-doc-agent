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

CONFIG_NAME = "enterprise-doc-config"
NAMESPACE_NAME = "enterprise-doc-agent-staging"
INGRESS_NAME = "enterprise-doc-web"
DATABASE_EGRESS_POLICY_NAME = "enterprise-doc-external-postgres-egress"
MODEL_PROVIDER = "openai_compatible"
CONFIG_HASH_ANNOTATION = "enterprise-doc-agent/config-sha256"
PREREQUISITE_HASH_ANNOTATION = "enterprise-doc-agent/prerequisites-sha256"
APPROVAL_ANNOTATION_PREFIX = "enterprise-doc-agent/approved-"
SERVICE_DEPLOYMENTS = {
    "api": "enterprise-doc-api",
    "worker": "enterprise-doc-worker",
    "consumer": "enterprise-doc-consumer",
    "web": "enterprise-doc-web",
}
CONFIG_CONSUMERS = {
    ("Deployment", "enterprise-doc-api"),
    ("Deployment", "enterprise-doc-worker"),
    ("Deployment", "enterprise-doc-consumer"),
    ("Job", "enterprise-doc-migrate"),
}
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
IMMUTABLE_IMAGE = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+"
    r"@sha256:[0-9a-f]{64}$"
)


def _ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _https_url(value: str, *, description: str):
    if _contains_control_character(value):
        raise ValueError(f"{description} must not contain control characters")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{description} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{description} must not contain credentials")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"{description} must contain a valid port") from error
    hostname = parsed.hostname
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError(f"{description} must not target localhost")
    address = _ip_address(hostname)
    if address is not None and not address.is_global:
        raise ValueError(f"{description} must target a global unicast address")
    return parsed


def _origin(value: str, *, description: str) -> str:
    parsed = _https_url(value, description=description)
    return f"{parsed.scheme}://{parsed.netloc}"


def _exact_origin(value: str, *, description: str) -> str:
    origin = _origin(value, description=description)
    if value.strip() != origin:
        raise ValueError(f"{description} must be an exact HTTPS origin without a path")
    return origin


def _model_base_url(value: str) -> str:
    if _contains_control_character(value):
        raise ValueError("model base URL must not contain control characters")
    normalized = value.strip()
    parsed = _https_url(normalized, description="model base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("model base URL must not contain a query or fragment")
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise ValueError("model base URL must identify an OpenAI-compatible /v1 endpoint")
    return normalized.rstrip("/")


def _model_name(value: str) -> str:
    if _contains_control_character(value):
        raise ValueError("model name must not contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("model name must contain 1-200 characters")
    return normalized


def _dns_label(value: str, *, description: str) -> str:
    if len(value) > 63 or not DNS_LABEL.fullmatch(value):
        raise ValueError(f"{description} must be a DNS label of 63 characters or fewer")
    return value


def _dns_hostname(value: str, *, description: str) -> str:
    if len(value) > 253 or _ip_address(value) is not None:
        raise ValueError(f"{description} must be a DNS hostname")
    labels = value.split(".")
    if not labels or any(not DNS_LABEL.fullmatch(label) or len(label) > 63 for label in labels):
        raise ValueError(f"{description} must be a DNS hostname")
    return value


def _global_host_cidr(value: str, *, description: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValueError(f"{description} must be a valid IP CIDR") from exc
    if network.prefixlen != network.max_prefixlen:
        raise ValueError(f"{description} must contain exactly one IP address")
    if not network.network_address.is_global:
        raise ValueError(f"{description} must contain a global unicast IP address")
    return str(network)


def _global_host_cidrs(value: str, *, description: str) -> list[str]:
    candidates = [item.strip() for item in value.split(",") if item.strip()]
    if not candidates:
        raise ValueError(f"{description} must contain at least one IP CIDR")
    cidrs = {_global_host_cidr(candidate, description=description) for candidate in candidates}
    return sorted(cidrs, key=lambda item: (ipaddress.ip_network(item).version, item))


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


def _container_image(document: dict[str, Any], *, description: str) -> str:
    try:
        containers = document["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"{description} must contain a Pod template") from error
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError(f"{description} must contain exactly one container")
    container = containers[0]
    if not isinstance(container, dict) or not isinstance(container.get("image"), str):
        raise ValueError(f"{description} container image must be a string")
    image = container["image"]
    if not IMMUTABLE_IMAGE.fullmatch(image):
        raise ValueError(f"{description} image must use an immutable sha256 digest")
    return image


def _approved_images(current: str, rollback: str | None, *, service: str) -> str:
    approved = [current]
    if rollback is None or not rollback.strip():
        return current
    candidate = rollback.strip()
    if not IMMUTABLE_IMAGE.fullmatch(candidate):
        raise ValueError(f"{service} rollback image must use an immutable sha256 digest")
    current_repository = current.rsplit("@", 1)[0]
    if candidate.rsplit("@", 1)[0] != current_repository:
        raise ValueError(f"{service} rollback image must use the current approved repository")
    if candidate != current:
        approved.append(candidate)
    return ",".join(approved)


def _prerequisite_digest(documents: list[dict[str, Any]]) -> str:
    prerequisites = [
        copy.deepcopy(document)
        for document in documents
        if document.get("kind") not in {"Deployment", "StatefulSet", "DaemonSet", "Job"}
    ]
    namespace = _single_document(prerequisites, kind="Namespace", name=NAMESPACE_NAME)
    metadata = namespace.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"Namespace/{NAMESPACE_NAME} metadata must be a mapping")
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        annotations.pop(PREREQUISITE_HASH_ANNOTATION, None)
    encoded = json.dumps(
        prerequisites,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def configure_manifest(
    source: Path,
    destination: Path,
    *,
    staging_base_url: str,
    object_store_endpoint: str,
    object_store_presign_endpoint: str,
    tls_secret_name: str,
    web_object_store_origins: str,
    database_egress_cidr: str,
    model_provider: str,
    model_base_url: str,
    model_name: str,
    rollback_api_image: str | None = None,
    rollback_worker_image: str | None = None,
    rollback_consumer_image: str | None = None,
    rollback_web_image: str | None = None,
) -> None:
    staging = _https_url(staging_base_url, description="staging base URL")
    staging_hostname = _dns_hostname(staging.hostname or "", description="staging host")
    object_store_endpoint = object_store_endpoint.strip()
    object_store_presign_endpoint = object_store_presign_endpoint.strip()
    _https_url(object_store_endpoint, description="object-store endpoint")
    presign_origin = _origin(
        object_store_presign_endpoint,
        description="object-store presign endpoint",
    )
    allowed_origins = {
        _exact_origin(item.strip(), description="Web image allowlist origin")
        for item in web_object_store_origins.split(",")
        if item.strip()
    }
    if presign_origin not in allowed_origins:
        raise ValueError(
            "Web image allowlist must include the object-store presign endpoint origin"
        )
    _dns_label(tls_secret_name, description="TLS secret name")
    database_cidrs = _global_host_cidrs(
        database_egress_cidr,
        description="database egress CIDRs",
    )
    if model_provider != MODEL_PROVIDER:
        raise ValueError(f"model provider must be {MODEL_PROVIDER}")
    normalized_model_base_url = _model_base_url(model_base_url)
    normalized_model_name = _model_name(model_name)

    documents = [
        document
        for document in yaml.safe_load_all(source.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]
    if any(document.get("kind") == "Secret" for document in documents):
        raise ValueError("rendered staging manifests must not contain Secret values")
    config = _single_document(documents, kind="ConfigMap", name=CONFIG_NAME)
    data = config.setdefault("data", {})
    if not isinstance(data, dict):
        raise ValueError(f"ConfigMap/{CONFIG_NAME} data must be a mapping")
    data["OBJECT_STORE__ENDPOINT"] = object_store_endpoint
    data["OBJECT_STORE__PRESIGN_ENDPOINT"] = object_store_presign_endpoint
    data["OBJECT_STORE__SECURE"] = "true"
    data["MODEL__PROVIDER"] = MODEL_PROVIDER
    data["MODEL__BASE_URL"] = normalized_model_base_url
    data["MODEL__MODEL_NAME"] = normalized_model_name

    config_digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    found_consumers: set[tuple[str, str]] = set()
    for document in documents:
        metadata = document.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
            continue
        identity = (str(document.get("kind")), metadata["name"])
        if identity not in CONFIG_CONSUMERS:
            continue
        spec = document.setdefault("spec", {})
        if not isinstance(spec, dict):
            raise ValueError(f"{identity[0]}/{identity[1]} spec must be a mapping")
        template = spec.setdefault("template", {})
        if not isinstance(template, dict):
            raise ValueError(f"{identity[0]}/{identity[1]} template must be a mapping")
        template_metadata = template.setdefault("metadata", {})
        if not isinstance(template_metadata, dict):
            raise ValueError(f"{identity[0]}/{identity[1]} template metadata must be a mapping")
        annotations = template_metadata.setdefault("annotations", {})
        if not isinstance(annotations, dict):
            raise ValueError(f"{identity[0]}/{identity[1]} annotations must be a mapping")
        annotations[CONFIG_HASH_ANNOTATION] = config_digest
        found_consumers.add(identity)
    missing_consumers = CONFIG_CONSUMERS - found_consumers
    if missing_consumers:
        rendered = ", ".join(f"{kind}/{name}" for kind, name in sorted(missing_consumers))
        raise ValueError(f"rendered manifests are missing config consumer(s): {rendered}")

    ingress = _single_document(documents, kind="Ingress", name=INGRESS_NAME)
    spec = ingress.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"Ingress/{INGRESS_NAME} spec must be a mapping")
    rules = spec.get("rules")
    tls = spec.get("tls")
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        raise ValueError(f"Ingress/{INGRESS_NAME} must contain exactly one rule")
    if not isinstance(tls, list) or len(tls) != 1 or not isinstance(tls[0], dict):
        raise ValueError(f"Ingress/{INGRESS_NAME} must contain exactly one TLS entry")
    rules[0]["host"] = staging_hostname
    tls[0]["hosts"] = [staging_hostname]
    tls[0]["secretName"] = tls_secret_name

    database_policy = _single_document(
        documents,
        kind="NetworkPolicy",
        name=DATABASE_EGRESS_POLICY_NAME,
    )
    policy_spec = database_policy.get("spec")
    if not isinstance(policy_spec, dict):
        raise ValueError(f"NetworkPolicy/{DATABASE_EGRESS_POLICY_NAME} spec must be a mapping")
    egress = policy_spec.get("egress")
    if not isinstance(egress, list) or len(egress) != 1 or not isinstance(egress[0], dict):
        raise ValueError(
            f"NetworkPolicy/{DATABASE_EGRESS_POLICY_NAME} must contain exactly one egress rule"
        )
    egress[0]["to"] = [{"ipBlock": {"cidr": cidr}} for cidr in database_cidrs]

    current_images: dict[str, str] = {}
    for service, deployment_name in SERVICE_DEPLOYMENTS.items():
        deployment = _single_document(
            documents,
            kind="Deployment",
            name=deployment_name,
        )
        current_images[service] = _container_image(
            deployment,
            description=f"Deployment/{deployment_name}",
        )
    migration = _single_document(documents, kind="Job", name="enterprise-doc-migrate")
    migration_image = _container_image(
        migration,
        description="Job/enterprise-doc-migrate",
    )
    if migration_image != current_images["api"]:
        raise ValueError("migration Job must use the current API image")

    namespace = _single_document(documents, kind="Namespace", name=NAMESPACE_NAME)
    namespace_metadata = namespace.setdefault("metadata", {})
    if not isinstance(namespace_metadata, dict):
        raise ValueError(f"Namespace/{NAMESPACE_NAME} metadata must be a mapping")
    namespace_annotations = namespace_metadata.setdefault("annotations", {})
    if not isinstance(namespace_annotations, dict):
        raise ValueError(f"Namespace/{NAMESPACE_NAME} annotations must be a mapping")
    rollback_images = {
        "api": rollback_api_image,
        "worker": rollback_worker_image,
        "consumer": rollback_consumer_image,
        "web": rollback_web_image,
    }
    approval_annotations = {
        f"{APPROVAL_ANNOTATION_PREFIX}staging-host": staging_hostname,
        f"{APPROVAL_ANNOTATION_PREFIX}tls-secret-name": tls_secret_name,
        f"{APPROVAL_ANNOTATION_PREFIX}database-egress-cidr": ",".join(database_cidrs),
        f"{APPROVAL_ANNOTATION_PREFIX}object-store-endpoint": object_store_endpoint,
        f"{APPROVAL_ANNOTATION_PREFIX}object-store-presign-endpoint": (
            object_store_presign_endpoint
        ),
        f"{APPROVAL_ANNOTATION_PREFIX}web-object-store-origins": ",".join(sorted(allowed_origins)),
        f"{APPROVAL_ANNOTATION_PREFIX}model-provider": MODEL_PROVIDER,
        f"{APPROVAL_ANNOTATION_PREFIX}model-base-url": normalized_model_base_url,
        f"{APPROVAL_ANNOTATION_PREFIX}model-name": normalized_model_name,
        f"{APPROVAL_ANNOTATION_PREFIX}config-sha256": config_digest,
    }
    for service, image in current_images.items():
        approval_annotations[f"{APPROVAL_ANNOTATION_PREFIX}{service}-images"] = _approved_images(
            image,
            rollback_images[service],
            service=service,
        )
    namespace_annotations.update(approval_annotations)
    namespace_annotations[PREREQUISITE_HASH_ANNOTATION] = _prerequisite_digest(documents)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bind external staging endpoints into a rendered Kubernetes manifest"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--staging-base-url", required=True)
    parser.add_argument("--object-store-endpoint", required=True)
    parser.add_argument("--object-store-presign-endpoint", required=True)
    parser.add_argument("--tls-secret-name", required=True)
    parser.add_argument("--web-object-store-origins", required=True)
    parser.add_argument("--database-egress-cidr", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--rollback-api-image")
    parser.add_argument("--rollback-worker-image")
    parser.add_argument("--rollback-consumer-image")
    parser.add_argument("--rollback-web-image")
    args = parser.parse_args()
    configure_manifest(
        args.input,
        args.output,
        staging_base_url=args.staging_base_url,
        object_store_endpoint=args.object_store_endpoint,
        object_store_presign_endpoint=args.object_store_presign_endpoint,
        tls_secret_name=args.tls_secret_name,
        web_object_store_origins=args.web_object_store_origins,
        database_egress_cidr=args.database_egress_cidr,
        model_provider=args.model_provider,
        model_base_url=args.model_base_url,
        model_name=args.model_name,
        rollback_api_image=args.rollback_api_image,
        rollback_worker_image=args.rollback_worker_image,
        rollback_consumer_image=args.rollback_consumer_image,
        rollback_web_image=args.rollback_web_image,
    )


if __name__ == "__main__":
    main()
