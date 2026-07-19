from __future__ import annotations

import argparse
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization

APP_SECRET_NAME = "enterprise-doc-secrets"
REGISTRY_SECRET_NAME = "enterprise-doc-registry"
APP_SECRET_KEYS = {
    "DATABASE__URL",
    "DATABASE__PASSWORD",
    "OBJECT_STORE__ACCESS_KEY",
    "OBJECT_STORE__SECRET_KEY",
    "AUTH__SIGNING_KEY",
    "MCP__SIGNING_SECRET",
    "MODEL__API_KEY",
}


class StagingSecretValidationError(ValueError):
    """Raised when staging prerequisites are missing or malformed."""


def _dns_name_covers_host(dns_name: str, host: str) -> bool:
    normalized_name = dns_name.rstrip(".").lower()
    normalized_host = host.rstrip(".").lower()
    if normalized_name == normalized_host:
        return True
    if not normalized_name.startswith("*."):
        return False
    suffix = normalized_name[2:]
    return (
        normalized_host.endswith(f".{suffix}")
        and normalized_host.count(".") == suffix.count(".") + 1
    )


def _items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise StagingSecretValidationError("secret response must be an object")
    if payload.get("kind") == "List":
        values = payload.get("items")
        if not isinstance(values, list):
            raise StagingSecretValidationError("SecretList items must be a list")
        return [item for item in values if isinstance(item, dict)]
    return [payload]


def _secret_map(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        raise StagingSecretValidationError("each Secret must have metadata.name")
    return item


def _decode_data(item: dict[str, Any]) -> dict[str, bytes]:
    raw = item.get("data")
    if not isinstance(raw, dict):
        raise StagingSecretValidationError(
            f"Secret/{item['metadata']['name']} must expose data keys"
        )
    decoded: dict[str, bytes] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise StagingSecretValidationError("Secret data keys and values must be strings")
        try:
            decoded[key] = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise StagingSecretValidationError(
                f"Secret/{item['metadata']['name']} contains invalid base64 data"
            ) from error
        if not decoded[key]:
            raise StagingSecretValidationError(
                f"Secret/{item['metadata']['name']}.{key} must not be empty"
            )
    return decoded


def _require_type(item: dict[str, Any], expected: str) -> None:
    if item.get("type") != expected:
        raise StagingSecretValidationError(
            f"Secret/{item['metadata']['name']} must have type {expected}"
        )


def _validate_tls(item: dict[str, Any], staging_host: str) -> dict[str, Any]:
    _require_type(item, "kubernetes.io/tls")
    data = _decode_data(item)
    if {"tls.crt", "tls.key"} - data.keys():
        raise StagingSecretValidationError("TLS Secret must contain tls.crt and tls.key")
    try:
        certificate = x509.load_pem_x509_certificate(data["tls.crt"])
        private_key = serialization.load_pem_private_key(data["tls.key"], password=None)
    except ValueError as error:
        raise StagingSecretValidationError("TLS certificate or key is not valid PEM") from error
    now = datetime.now(UTC)
    not_before = certificate.not_valid_before_utc
    not_after = certificate.not_valid_after_utc
    if not_before > now or not_after <= now:
        raise StagingSecretValidationError("TLS certificate is not currently valid")
    try:
        names = set(
            certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        )
    except x509.ExtensionNotFound as error:
        raise StagingSecretValidationError("TLS certificate must contain DNS SANs") from error
    if not any(_dns_name_covers_host(name, staging_host) for name in names):
        raise StagingSecretValidationError("TLS certificate SAN does not cover staging host")
    cert_public = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_public = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_public != key_public:
        raise StagingSecretValidationError("TLS private key does not match certificate")
    return {
        "name": item["metadata"]["name"],
        "type": item["type"],
        "keys": sorted(data),
        "dns_names": sorted(names),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "values_redacted": True,
    }


def validate_staging_secrets(
    payload: Any,
    *,
    staging_host: str,
    tls_secret_name: str,
) -> dict[str, Any]:
    by_name = {item["metadata"]["name"]: _secret_map(item) for item in _items(payload)}
    required = {APP_SECRET_NAME, REGISTRY_SECRET_NAME, tls_secret_name}
    missing = required - by_name.keys()
    if missing:
        raise StagingSecretValidationError(
            "missing required Secret(s): " + ", ".join(sorted(missing))
        )

    app = by_name[APP_SECRET_NAME]
    _require_type(app, "Opaque")
    app_data = _decode_data(app)
    missing_app = APP_SECRET_KEYS - app_data.keys()
    if missing_app:
        raise StagingSecretValidationError(
            "application Secret missing key(s): " + ", ".join(sorted(missing_app))
        )
    registry = by_name[REGISTRY_SECRET_NAME]
    _require_type(registry, "kubernetes.io/dockerconfigjson")
    registry_data = _decode_data(registry)
    if ".dockerconfigjson" not in registry_data:
        raise StagingSecretValidationError("registry Secret must contain .dockerconfigjson")
    try:
        json.loads(registry_data[".dockerconfigjson"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StagingSecretValidationError(
            "registry .dockerconfigjson must be valid JSON"
        ) from error

    return {
        "schema_version": 1,
        "status": "passed",
        "staging_host": staging_host,
        "secrets": [
            {
                "name": APP_SECRET_NAME,
                "type": app["type"],
                "keys": sorted(app_data),
                "values_redacted": True,
            },
            {
                "name": REGISTRY_SECRET_NAME,
                "type": registry["type"],
                "keys": sorted(registry_data),
                "values_redacted": True,
            },
            _validate_tls(by_name[tls_secret_name], staging_host),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and redact staging Secret prerequisites")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--staging-host", required=True)
    parser.add_argument("--tls-secret-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = validate_staging_secrets(
            json.loads(args.input.read_text(encoding="utf-8")),
            staging_host=args.staging_host,
            tls_secret_name=args.tls_secret_name,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError, StagingSecretValidationError) as error:
        raise SystemExit(str(error)) from error
    print(
        json.dumps(
            {"status": report["status"], "secret_count": len(report["secrets"])}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
