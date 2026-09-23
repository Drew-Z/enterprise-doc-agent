from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse

import yaml  # type: ignore[import-untyped]

CONFIG_NAME = "enterprise-doc-config"
NAMESPACE_NAME = "enterprise-doc-agent-staging"
INGRESS_NAME = "enterprise-doc-web"
DATABASE_EGRESS_POLICY_NAME = "enterprise-doc-external-postgres-egress"
MODEL_PROVIDER = "openai_compatible"
MODEL_FALLBACK_SECRET_KEY = "MODEL__FALLBACK_API_KEY"
EMBEDDING_DIMENSION = "1024"
EMBEDDING_VERSION = "2"
EMBEDDING_QUERY_INSTRUCTION = (
    "Given a user question about enterprise documents, retrieve relevant passages "
    "that answer the question"
)
OBJECT_STORE_CHECKSUM_MODES = {"native_sha256", "readback_sha256"}
CONFIG_HASH_ANNOTATION = "enterprise-doc-agent/config-sha256"
PROMETHEUS_CONFIG_HASH_ANNOTATION = "enterprise-doc-agent/prometheus-config-sha256"
PREREQUISITE_HASH_ANNOTATION = "enterprise-doc-agent/prerequisites-sha256"
APPROVAL_ANNOTATION_PREFIX = "enterprise-doc-agent/approved-"
PROMETHEUS_CONFIG_NAME = "enterprise-doc-prometheus-config"
PROMETHEUS_DEPLOYMENT_NAME = "enterprise-doc-prometheus"
PROMETHEUS_IMAGE = (
    "quay.io/prometheus/prometheus@sha256:"
    "63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996"
)
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
    ("Job", "enterprise-doc-embedding-rollout"),
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


def _https_url(value: str, *, description: str) -> ParseResult:
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


def _model_version(value: str) -> str:
    if _contains_control_character(value):
        raise ValueError("model version must not contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("model version must contain 1-100 characters")
    return normalized


def _model_timeout_seconds(value: str) -> str:
    normalized = value.strip()
    try:
        timeout = float(normalized)
    except ValueError as error:
        raise ValueError("model timeout must be a number greater than 0 and at most 300") from error
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("model timeout must be a number greater than 0 and at most 300")
    return format(timeout, "g")


def _presales_environment(
    *,
    generation_enabled: str,
    model_route: str,
    model_timeout_seconds: str | None,
    row_timeout_seconds: str,
    fallback_configured: bool,
) -> dict[str, str]:
    enabled = generation_enabled.strip()
    route = model_route.strip()
    if enabled not in {"true", "false"}:
        raise ValueError("presales generation enabled must be true or false")
    if route not in {"primary", "fallback"}:
        raise ValueError("presales model route must be primary or fallback")
    if route == "fallback" and not fallback_configured:
        raise ValueError("presales fallback selection requires a configured fallback model route")

    def timeout(value: str, description: str) -> str:
        normalized = value.strip()
        try:
            seconds = float(normalized)
        except ValueError as error:
            raise ValueError(
                f"presales {description} must be finite, greater than 0 and at most 180"
            ) from error
        if not math.isfinite(seconds) or not 0 < seconds <= 180:
            raise ValueError(
                f"presales {description} must be finite, greater than 0 and at most 180"
            )
        return normalized

    row_timeout = timeout(row_timeout_seconds, "row timeout")
    result = {
        "PRESALES__GENERATION_ENABLED": enabled,
        "PRESALES__MODEL_ROUTE": route,
        "PRESALES__ROW_TIMEOUT_SECONDS": row_timeout,
    }
    if model_timeout_seconds and model_timeout_seconds.strip():
        model_timeout = timeout(model_timeout_seconds, "model timeout")
        if float(model_timeout) >= float(row_timeout):
            raise ValueError("presales model timeout must be less than the row timeout")
        result["PRESALES__MODEL_TIMEOUT_SECONDS"] = model_timeout
    return result


def _embedding_version(value: str) -> str:
    normalized = value.strip()
    if not normalized.isdigit() or not 1 <= int(normalized) <= 1000:
        raise ValueError("embedding version must be an integer from 1 to 1000")
    return str(int(normalized))


def _embedding_base_url(value: str) -> str:
    if _contains_control_character(value):
        raise ValueError("embedding base URL must not contain control characters")
    normalized = value.strip()
    parsed = _https_url(normalized, description="embedding base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("embedding base URL must not contain a query or fragment")
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise ValueError("embedding base URL must identify an OpenAI-compatible /v1 endpoint")
    return normalized.rstrip("/")


def _object_store_checksum_mode(value: str) -> str:
    normalized = value.strip()
    if normalized not in OBJECT_STORE_CHECKSUM_MODES:
        raise ValueError("object-store checksum mode is unsupported")
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


def _browser_oidc_url(value: str, *, description: str) -> ParseResult:
    parsed = _https_url(value, description=description)
    _dns_hostname(parsed.hostname or "", description=description)
    if (
        len(value) > 2048
        or any(ord(character) <= 32 for character in value)
        or "\\" in value
        or value != f"https://{parsed.netloc}{parsed.path}"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{description} must be an exact HTTPS URL without query or credentials")
    return parsed


def browser_auth_environment(
    web_origin: str,
    issuer: str | None,
    client_id: str | None = None,
    *,
    oidc_config: str | None = None,
    provider: str = "oidc",
) -> dict[str, str]:
    """Bind the selected protocol; GitHub uses official OAuth, not OIDC discovery."""
    if provider not in {"oidc", "github"}:
        raise ValueError("unsupported browser identity provider")
    if not issuer:
        if client_id or oidc_config is not None or provider != "oidc":
            raise ValueError("browser client ID and OIDC configuration require an issuer")
        return {"BROWSER_AUTH__ENABLED": "false"}
    if web_origin != _exact_origin(web_origin, description="browser Web origin"):
        raise ValueError("browser Web origin must be exact")
    parsed = _browser_oidc_url(issuer, description="browser issuer")
    if len(issuer) > 512:
        raise ValueError("browser issuer must contain at most 512 characters")
    if parsed.hostname == urlparse(web_origin).hostname:
        raise ValueError("browser issuer must use a separate identity hostname")
    selected_client = "docagent-web" if client_id is None else client_id
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", selected_client):
        raise ValueError("browser client ID must be an exact non-empty identifier")
    if provider == "github":
        if issuer != "https://github.com" or not client_id or oidc_config is not None:
            raise ValueError(
                "GitHub requires its official issuer, client ID and no OIDC configuration"
            )
        return {
            "BROWSER_AUTH__ENABLED": "true",
            "BROWSER_AUTH__PROVIDER": "github",
            "BROWSER_AUTH__WEB_ORIGIN": web_origin,
            "BROWSER_AUTH__ISSUER": issuer,
            "BROWSER_AUTH__AUTHORIZATION_ENDPOINT": issuer + "/login/oauth/authorize",
            "BROWSER_AUTH__TOKEN_ENDPOINT": issuer + "/login/oauth/access_token",
            "BROWSER_AUTH__CLIENT_ID": selected_client,
        }
    if oidc_config is None:
        realm = re.fullmatch(r"/realms/([A-Za-z0-9_-]{1,128})", parsed.path)
        if realm is None or realm.group(1).lower() == "master":
            raise ValueError("browser issuer must be an exact HTTPS Keycloak application realm")
        endpoints = {
            "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
            "token_endpoint": issuer + "/protocol/openid-connect/token",
            "jwks_uri": issuer + "/protocol/openid-connect/certs",
        }
        algorithms = ["RS256"]
    else:
        if not client_id or len(oidc_config) > 8192:
            raise ValueError("explicit OIDC configuration requires a client ID and bounded JSON")
        try:
            explicit = json.loads(oidc_config)
        except ValueError:
            raise ValueError("browser OIDC configuration must be valid JSON") from None
        required = {"authorization_endpoint", "token_endpoint", "jwks_uri", "algorithms"}
        if not isinstance(explicit, dict) or set(explicit) != required:
            raise ValueError("browser OIDC configuration requires exactly endpoints and algorithms")
        algorithms = explicit["algorithms"]
        if (
            not isinstance(algorithms, list)
            or not algorithms
            or any(algorithm not in ("RS256", "ES256") for algorithm in algorithms)
            or len(algorithms) != len(set(algorithms))
        ):
            raise ValueError("browser OIDC algorithms must select RS256 and/or ES256")
        endpoints = {}
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = explicit[key]
            if not isinstance(value, str):
                raise ValueError("browser OIDC endpoints must be HTTPS URLs")
            endpoint = _browser_oidc_url(value, description="browser OIDC endpoint")
            if endpoint.netloc != parsed.netloc:
                raise ValueError("browser OIDC endpoints must share the issuer origin")
            endpoints[key] = value
    return {
        "BROWSER_AUTH__ENABLED": "true",
        "BROWSER_AUTH__WEB_ORIGIN": web_origin,
        "BROWSER_AUTH__ISSUER": issuer,
        "BROWSER_AUTH__AUTHORIZATION_ENDPOINT": endpoints["authorization_endpoint"],
        "BROWSER_AUTH__TOKEN_ENDPOINT": endpoints["token_endpoint"],
        "BROWSER_AUTH__JWKS_URL": endpoints["jwks_uri"],
        "BROWSER_AUTH__CLIENT_ID": selected_client,
        "BROWSER_AUTH__ALGORITHMS": json.dumps(algorithms),
    }


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


def _optional_document(
    documents: list[dict[str, Any]], *, kind: str, name: str
) -> dict[str, Any] | None:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and isinstance(document.get("metadata"), dict)
        and document["metadata"].get("name") == name
    ]
    if len(matches) > 1:
        raise ValueError(f"rendered manifests must contain at most one {kind}/{name}")
    return matches[0] if matches else None


def _container_image(document: dict[str, Any], *, description: str) -> str:
    try:
        containers = document["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"{description} must contain a Pod template") from error
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError(f"{description} must contain exactly one container")
    container = containers[0]
    if not isinstance(container, dict):
        raise ValueError(f"{description} container image must be a string")
    image = container.get("image")
    if not isinstance(image, str):
        raise ValueError(f"{description} container image must be a string")
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
    browser_auth_issuer: str | None = None,
    browser_auth_client_id: str | None = None,
    browser_auth_oidc_config: str | None = None,
    browser_auth_provider: str = "oidc",
    fallback_model_base_url: str | None = None,
    fallback_model_name: str | None = None,
    fallback_model_version: str | None = None,
    fallback_model_timeout_seconds: str | None = None,
    presales_generation_enabled: str = "false",
    presales_model_route: str = "primary",
    presales_model_timeout_seconds: str | None = None,
    presales_row_timeout_seconds: str = "90",
    demo_enabled: str = "false",
    embedding_base_url: str = "https://embedding.example.invalid/v1",
    embedding_model_name: str = "staging-embedding",
    embedding_version: str = EMBEDDING_VERSION,
    object_store_checksum_mode: str = "native_sha256",
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
    fallback_base_url_value = (
        fallback_model_base_url.strip() if fallback_model_base_url is not None else ""
    )
    fallback_name_value = fallback_model_name.strip() if fallback_model_name is not None else ""
    fallback_version_value = (
        fallback_model_version.strip() if fallback_model_version is not None else ""
    )
    fallback_timeout_value = (
        fallback_model_timeout_seconds.strip() if fallback_model_timeout_seconds is not None else ""
    )
    if bool(fallback_base_url_value) != bool(fallback_name_value):
        raise ValueError("fallback model route requires both a base URL and a model name")
    if (fallback_version_value or fallback_timeout_value) and not fallback_base_url_value:
        raise ValueError("fallback model version and timeout require a configured fallback route")
    normalized_fallback_base_url = (
        _model_base_url(fallback_base_url_value) if fallback_base_url_value else None
    )
    normalized_fallback_name = _model_name(fallback_name_value) if fallback_name_value else None
    normalized_fallback_version = (
        _model_version(fallback_version_value) if fallback_version_value else None
    )
    normalized_fallback_timeout = (
        _model_timeout_seconds(fallback_timeout_value) if fallback_timeout_value else None
    )
    presales_config = _presales_environment(
        generation_enabled=presales_generation_enabled,
        model_route=presales_model_route,
        model_timeout_seconds=presales_model_timeout_seconds,
        row_timeout_seconds=presales_row_timeout_seconds,
        fallback_configured=normalized_fallback_base_url is not None,
    )
    normalized_embedding_base_url = _embedding_base_url(embedding_base_url)
    normalized_embedding_model_name = _model_name(embedding_model_name)
    normalized_embedding_version = _embedding_version(embedding_version)
    normalized_checksum_mode = _object_store_checksum_mode(object_store_checksum_mode)
    browser_config = browser_auth_environment(
        staging_base_url,
        browser_auth_issuer,
        browser_auth_client_id,
        oidc_config=browser_auth_oidc_config,
        provider=browser_auth_provider,
    )
    demo_enabled = demo_enabled.strip().lower()
    if demo_enabled not in {"true", "false"}:
        raise ValueError("demo enabled must be true or false")
    if demo_enabled == "true" and (
        browser_config.get("BROWSER_AUTH__ENABLED") != "true"
        or presales_config["PRESALES__GENERATION_ENABLED"] != "true"
    ):
        raise ValueError("demo requires browser sessions and presales generation")

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
    if "BROWSER_AUTH__CLIENT_SECRET" in data:
        raise ValueError("browser client secret must come only from enterprise-doc-secrets")
    for key in (
        "ENABLED",
        "PROVIDER",
        "WEB_ORIGIN",
        "ISSUER",
        "AUTHORIZATION_ENDPOINT",
        "TOKEN_ENDPOINT",
        "JWKS_URL",
        "CLIENT_ID",
        "ALGORITHMS",
    ):
        data.pop(f"BROWSER_AUTH__{key}", None)
    data.update(browser_config)
    data["OBJECT_STORE__ENDPOINT"] = object_store_endpoint
    data["OBJECT_STORE__PRESIGN_ENDPOINT"] = object_store_presign_endpoint
    data["OBJECT_STORE__SECURE"] = "true"
    data["OBJECT_STORE__MULTIPART_CHECKSUM_MODE"] = normalized_checksum_mode
    data["MODEL__PROVIDER"] = MODEL_PROVIDER
    data["MODEL__BASE_URL"] = normalized_model_base_url
    data["MODEL__MODEL_NAME"] = normalized_model_name
    for fallback_key in (
        "MODEL__FALLBACK_PROVIDER",
        "MODEL__FALLBACK_BASE_URL",
        "MODEL__FALLBACK_MODEL_NAME",
        "MODEL__FALLBACK_MODEL_VERSION",
        "MODEL__FALLBACK_TIMEOUT_SECONDS",
    ):
        data.pop(fallback_key, None)
    if normalized_fallback_base_url is not None and normalized_fallback_name is not None:
        data["MODEL__FALLBACK_PROVIDER"] = MODEL_PROVIDER
        data["MODEL__FALLBACK_BASE_URL"] = normalized_fallback_base_url
        data["MODEL__FALLBACK_MODEL_NAME"] = normalized_fallback_name
        if normalized_fallback_version is not None:
            data["MODEL__FALLBACK_MODEL_VERSION"] = normalized_fallback_version
        if normalized_fallback_timeout is not None:
            data["MODEL__FALLBACK_TIMEOUT_SECONDS"] = normalized_fallback_timeout
    data.pop("PRESALES__MODEL_TIMEOUT_SECONDS", None)
    data.update(presales_config)
    data["DEMO__ENABLED"] = demo_enabled
    data["EMBEDDING__PROVIDER"] = "openai_compatible"
    data["EMBEDDING__BASE_URL"] = normalized_embedding_base_url
    data["EMBEDDING__MODEL_NAME"] = normalized_embedding_model_name
    data["EMBEDDING__DIMENSION"] = EMBEDDING_DIMENSION
    data["EMBEDDING__VERSION"] = normalized_embedding_version
    data["EMBEDDING__SEND_DIMENSIONS"] = "true"
    data["EMBEDDING__QUERY_INSTRUCTION"] = EMBEDDING_QUERY_INSTRUCTION
    data["RETRIEVAL__REQUIRE_VECTOR_EVIDENCE"] = "true"

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

    prometheus_config = _optional_document(
        documents,
        kind="ConfigMap",
        name=PROMETHEUS_CONFIG_NAME,
    )
    prometheus_deployment = _optional_document(
        documents,
        kind="Deployment",
        name=PROMETHEUS_DEPLOYMENT_NAME,
    )
    if (prometheus_config is None) != (prometheus_deployment is None):
        raise ValueError("Prometheus ConfigMap and Deployment must be rendered together")
    if prometheus_config is not None and prometheus_deployment is not None:
        prometheus_data = prometheus_config.get("data")
        if not isinstance(prometheus_data, dict) or not prometheus_data:
            raise ValueError(f"ConfigMap/{PROMETHEUS_CONFIG_NAME} data must be a mapping")
        prometheus_digest = hashlib.sha256(
            json.dumps(prometheus_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        prometheus_template = prometheus_deployment["spec"]["template"]
        prometheus_annotations = prometheus_template.setdefault("metadata", {}).setdefault(
            "annotations", {}
        )
        if not isinstance(prometheus_annotations, dict):
            raise ValueError(
                f"Deployment/{PROMETHEUS_DEPLOYMENT_NAME} annotations must be a mapping"
            )
        prometheus_annotations[PROMETHEUS_CONFIG_HASH_ANNOTATION] = prometheus_digest
        prometheus_image = _container_image(
            prometheus_deployment,
            description=f"Deployment/{PROMETHEUS_DEPLOYMENT_NAME}",
        )
        if prometheus_image != PROMETHEUS_IMAGE:
            raise ValueError("Prometheus Deployment must use the reviewed immutable image")

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
    embedding_rollout = _single_document(
        documents,
        kind="Job",
        name="enterprise-doc-embedding-rollout",
    )
    embedding_rollout_image = _container_image(
        embedding_rollout,
        description="Job/enterprise-doc-embedding-rollout",
    )
    if embedding_rollout_image != current_images["api"]:
        raise ValueError("embedding rollout Job must use the current API image")

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
        f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-enabled": browser_config[
            "BROWSER_AUTH__ENABLED"
        ],
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
        f"{APPROVAL_ANNOTATION_PREFIX}embedding-base-url": normalized_embedding_base_url,
        f"{APPROVAL_ANNOTATION_PREFIX}embedding-model-name": normalized_embedding_model_name,
        f"{APPROVAL_ANNOTATION_PREFIX}embedding-dimension": EMBEDDING_DIMENSION,
        f"{APPROVAL_ANNOTATION_PREFIX}embedding-version": normalized_embedding_version,
        f"{APPROVAL_ANNOTATION_PREFIX}config-sha256": config_digest,
        f"{APPROVAL_ANNOTATION_PREFIX}prometheus-images": PROMETHEUS_IMAGE,
    }
    for suffix in ("provider", "issuer", "client-id", "client-secret-key"):
        namespace_annotations.pop(f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-{suffix}", None)
    if browser_config["BROWSER_AUTH__ENABLED"] == "true":
        approval_annotations.update(
            {
                f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-provider": browser_auth_provider,
                f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-issuer": browser_config[
                    "BROWSER_AUTH__ISSUER"
                ],
                f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-client-id": browser_config[
                    "BROWSER_AUTH__CLIENT_ID"
                ],
                f"{APPROVAL_ANNOTATION_PREFIX}browser-auth-client-secret-key": (
                    "BROWSER_AUTH__CLIENT_SECRET"
                ),
            }
        )
    fallback_approval_keys = {
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-provider",
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-base-url",
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-name",
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-version",
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-timeout-seconds",
        f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-secret-key",
    }
    for key in fallback_approval_keys:
        namespace_annotations.pop(key, None)
    if normalized_fallback_base_url is not None and normalized_fallback_name is not None:
        approval_annotations.update(
            {
                f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-provider": MODEL_PROVIDER,
                f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-base-url": (
                    normalized_fallback_base_url
                ),
                f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-name": normalized_fallback_name,
                f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-secret-key": (
                    MODEL_FALLBACK_SECRET_KEY
                ),
            }
        )
        if normalized_fallback_version is not None:
            approval_annotations[f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-version"] = (
                normalized_fallback_version
            )
        if normalized_fallback_timeout is not None:
            approval_annotations[f"{APPROVAL_ANNOTATION_PREFIX}model-fallback-timeout-seconds"] = (
                normalized_fallback_timeout
            )
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
    parser.add_argument(
        "--object-store-checksum-mode",
        choices=sorted(OBJECT_STORE_CHECKSUM_MODES),
        default="native_sha256",
    )
    parser.add_argument("--tls-secret-name", required=True)
    parser.add_argument("--web-object-store-origins", required=True)
    parser.add_argument("--database-egress-cidr", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--browser-auth-issuer")
    parser.add_argument("--browser-auth-client-id")
    parser.add_argument("--browser-auth-oidc-config")
    parser.add_argument("--browser-auth-provider", choices=("oidc", "github"), default="oidc")
    parser.add_argument("--fallback-model-base-url")
    parser.add_argument("--fallback-model-name")
    parser.add_argument("--fallback-model-version")
    parser.add_argument("--fallback-model-timeout-seconds")
    parser.add_argument("--presales-generation-enabled", choices=("true", "false"), default="false")
    parser.add_argument(
        "--presales-model-route", choices=("primary", "fallback"), default="primary"
    )
    parser.add_argument("--presales-model-timeout-seconds")
    parser.add_argument("--presales-row-timeout-seconds", default="90")
    parser.add_argument("--demo-enabled", choices=("true", "false"), default="false")
    parser.add_argument("--embedding-base-url", required=True)
    parser.add_argument("--embedding-model-name", required=True)
    parser.add_argument("--embedding-version", default=EMBEDDING_VERSION)
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
        object_store_checksum_mode=args.object_store_checksum_mode,
        tls_secret_name=args.tls_secret_name,
        web_object_store_origins=args.web_object_store_origins,
        database_egress_cidr=args.database_egress_cidr,
        model_provider=args.model_provider,
        model_base_url=args.model_base_url,
        model_name=args.model_name,
        browser_auth_issuer=args.browser_auth_issuer,
        browser_auth_client_id=args.browser_auth_client_id,
        browser_auth_oidc_config=args.browser_auth_oidc_config,
        browser_auth_provider=args.browser_auth_provider,
        fallback_model_base_url=args.fallback_model_base_url,
        fallback_model_name=args.fallback_model_name,
        fallback_model_version=args.fallback_model_version,
        fallback_model_timeout_seconds=args.fallback_model_timeout_seconds,
        presales_generation_enabled=args.presales_generation_enabled,
        presales_model_route=args.presales_model_route,
        presales_model_timeout_seconds=args.presales_model_timeout_seconds,
        presales_row_timeout_seconds=args.presales_row_timeout_seconds,
        demo_enabled=args.demo_enabled,
        embedding_base_url=args.embedding_base_url,
        embedding_model_name=args.embedding_model_name,
        embedding_version=args.embedding_version,
        rollback_api_image=args.rollback_api_image,
        rollback_worker_image=args.rollback_worker_image,
        rollback_consumer_image=args.rollback_consumer_image,
        rollback_web_image=args.rollback_web_image,
    )


if __name__ == "__main__":
    main()
