from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "configure_staging_manifest.py"
SPEC = spec_from_file_location("configure_staging_manifest_test_module", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_staging_manifest = module_from_spec(SPEC)
sys.modules[SPEC.name] = configure_staging_manifest
SPEC.loader.exec_module(configure_staging_manifest)


def _write_template(path: Path) -> None:
    documents = [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "enterprise-doc-config"},
            "data": {
                "OBJECT_STORE__ENDPOINT": "https://objects.example.invalid",
                "OBJECT_STORE__PRESIGN_ENDPOINT": "https://objects.example.invalid",
                "OBJECT_STORE__SECURE": "true",
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "enterprise-doc-web"},
            "spec": {
                "rules": [
                    {
                        "host": "staging.example.invalid",
                        "http": {"paths": []},
                    }
                ],
                "tls": [
                    {
                        "hosts": ["staging.example.invalid"],
                        "secretName": "enterprise-doc-staging-tls",
                    }
                ],
            },
        },
    ]
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")


def test_configure_manifest_binds_https_hosts_without_secret_data(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "staging.yaml"
    _write_template(source)

    configure_staging_manifest.configure_manifest(
        source,
        destination,
        staging_base_url="https://staging.example.com",
        object_store_endpoint="https://objects.internal.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        web_object_store_origins="https://objects.example.com,https://cdn.example.com",
    )

    documents = [
        item
        for item in yaml.safe_load_all(destination.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]
    config = next(item for item in documents if item["kind"] == "ConfigMap")
    assert config["data"]["OBJECT_STORE__ENDPOINT"] == "https://objects.internal.example.com"
    assert config["data"]["OBJECT_STORE__PRESIGN_ENDPOINT"] == "https://objects.example.com"
    assert config["data"]["OBJECT_STORE__SECURE"] == "true"
    ingress = next(item for item in documents if item["kind"] == "Ingress")
    assert ingress["spec"]["rules"][0]["host"] == "staging.example.com"
    assert ingress["spec"]["tls"] == [
        {
            "hosts": ["staging.example.com"],
            "secretName": "enterprise-doc-staging-tls",
        }
    ]
    assert all(item["kind"] != "Secret" for item in documents)


def test_configure_manifest_rejects_unlisted_or_plaintext_origins(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="HTTPS"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "http.yaml",
            staging_base_url="http://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com",
        )

    with pytest.raises(ValueError, match="Web image allowlist"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "mismatch.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://other.example.com",
        )

    with pytest.raises(ValueError, match="exact HTTPS origin"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "path-origin.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com/uploads",
        )

    with pytest.raises(ValueError, match="63 characters"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "long-secret.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="a" * 64,
            web_object_store_origins="https://objects.example.com",
        )

    with pytest.raises(ValueError, match="DNS hostname"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "bad-host.yaml",
            staging_base_url="https://bad_host.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com",
        )
