from __future__ import annotations

import argparse
import ipaddress
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

CONFIG_NAME = "enterprise-doc-config"
INGRESS_NAME = "enterprise-doc-web"
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


def _ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _https_url(value: str, *, description: str):
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{description} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{description} must not contain credentials")
    hostname = parsed.hostname
    if hostname == "localhost":
        raise ValueError(f"{description} must not target localhost")
    address = _ip_address(hostname)
    if address is not None and address.is_loopback:
        raise ValueError(f"{description} must not target a loopback address")
    return parsed


def _origin(value: str, *, description: str) -> str:
    parsed = _https_url(value, description=description)
    return f"{parsed.scheme}://{parsed.netloc}"


def _exact_origin(value: str, *, description: str) -> str:
    origin = _origin(value, description=description)
    if value.strip() != origin:
        raise ValueError(f"{description} must be an exact HTTPS origin without a path")
    return origin


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


def configure_manifest(
    source: Path,
    destination: Path,
    *,
    staging_base_url: str,
    object_store_endpoint: str,
    object_store_presign_endpoint: str,
    tls_secret_name: str,
    web_object_store_origins: str,
) -> None:
    staging = _https_url(staging_base_url, description="staging base URL")
    staging_hostname = _dns_hostname(staging.hostname or "", description="staging host")
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

    documents = [
        document
        for document in yaml.safe_load_all(source.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]
    config = _single_document(documents, kind="ConfigMap", name=CONFIG_NAME)
    data = config.setdefault("data", {})
    if not isinstance(data, dict):
        raise ValueError(f"ConfigMap/{CONFIG_NAME} data must be a mapping")
    data["OBJECT_STORE__ENDPOINT"] = object_store_endpoint
    data["OBJECT_STORE__PRESIGN_ENDPOINT"] = object_store_presign_endpoint
    data["OBJECT_STORE__SECURE"] = "true"

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
    args = parser.parse_args()
    configure_manifest(
        args.input,
        args.output,
        staging_base_url=args.staging_base_url,
        object_store_endpoint=args.object_store_endpoint,
        object_store_presign_endpoint=args.object_store_presign_endpoint,
        tls_secret_name=args.tls_secret_name,
        web_object_store_origins=args.web_object_store_origins,
    )


if __name__ == "__main__":
    main()
