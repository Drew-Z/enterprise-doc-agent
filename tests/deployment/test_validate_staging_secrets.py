from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from scripts.validate_staging_secrets import (
    APP_SECRET_KEYS,
    MODEL_FALLBACK_SECRET_KEY,
    StagingSecretValidationError,
    validate_staging_secrets,
)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _tls_secret(
    host: str = "staging.example.com", *, dns_name: str | None = None
) -> dict[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns_name or host)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "enterprise-doc-staging-tls"},
        "type": "kubernetes.io/tls",
        "data": {
            "tls.crt": _b64(certificate.public_bytes(serialization.Encoding.PEM)),
            "tls.key": _b64(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            ),
        },
    }


def _payload() -> dict[str, object]:
    app = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "enterprise-doc-secrets"},
        "type": "Opaque",
        "data": {key: _b64(f"value-{key}".encode()) for key in APP_SECRET_KEYS},
    }
    registry = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "enterprise-doc-registry"},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {".dockerconfigjson": _b64(b'{"auths": {}}')},
    }
    return {"apiVersion": "v1", "kind": "List", "items": [app, registry, _tls_secret()]}


def test_validate_staging_secrets_returns_only_redacted_metadata() -> None:
    report = validate_staging_secrets(
        _payload(), staging_host="staging.example.com", tls_secret_name="enterprise-doc-staging-tls"
    )
    rendered = json.dumps(report)
    assert report["status"] == "passed"
    assert "value-DATABASE__PASSWORD" not in rendered
    assert "values_redacted" in rendered


def test_validate_staging_secrets_rejects_missing_application_key() -> None:
    payload = _payload()
    app = payload["items"][0]
    assert isinstance(app, dict)
    del app["data"][next(iter(APP_SECRET_KEYS))]
    with pytest.raises(StagingSecretValidationError, match="missing key"):
        validate_staging_secrets(
            payload,
            staging_host="staging.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
        )


def test_validate_staging_secrets_requires_fallback_key_only_when_enabled() -> None:
    payload = _payload()

    validate_staging_secrets(
        payload,
        staging_host="staging.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
    )
    with pytest.raises(StagingSecretValidationError, match=MODEL_FALLBACK_SECRET_KEY):
        validate_staging_secrets(
            payload,
            staging_host="staging.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            require_model_fallback_api_key=True,
        )

    app = payload["items"][0]
    assert isinstance(app, dict)
    app["data"][MODEL_FALLBACK_SECRET_KEY] = _b64(b"fallback-secret")
    report = validate_staging_secrets(
        payload,
        staging_host="staging.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        require_model_fallback_api_key=True,
    )
    assert report["status"] == "passed"


def test_validate_staging_secrets_rejects_wrong_tls_host() -> None:
    with pytest.raises(StagingSecretValidationError, match="SAN"):
        validate_staging_secrets(
            _payload(),
            staging_host="other.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
        )


def test_validate_staging_secrets_accepts_single_label_wildcard_san() -> None:
    payload = _payload()
    payload["items"][-1] = _tls_secret(dns_name="*.example.com")  # type: ignore[index]

    report = validate_staging_secrets(
        payload,
        staging_host="staging.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
    )

    assert report["status"] == "passed"


def test_validate_staging_secrets_rejects_nested_wildcard_match() -> None:
    payload = _payload()
    payload["items"][-1] = _tls_secret(dns_name="*.example.com")  # type: ignore[index]

    with pytest.raises(StagingSecretValidationError, match="SAN"):
        validate_staging_secrets(
            payload,
            staging_host="api.staging.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
        )
