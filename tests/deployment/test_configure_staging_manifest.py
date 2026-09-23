from __future__ import annotations

import json
import re
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

IMAGE_DIGESTS = {
    "enterprise-doc-api": "a" * 64,
    "enterprise-doc-worker": "b" * 64,
    "enterprise-doc-consumer": "c" * 64,
    "enterprise-doc-web": "d" * 64,
}


def _image(name: str, digest: str | None = None) -> str:
    service = name.removeprefix("enterprise-doc-")
    return f"registry.example.com/enterprise-doc-{service}@sha256:{digest or IMAGE_DIGESTS[name]}"


def _write_template(path: Path) -> None:
    documents = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "enterprise-doc-agent-staging",
                "annotations": {"enterprise-doc-agent/deployment-profile": "tiny-single-node"},
            },
        },
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
        *[
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name},
                "spec": {
                    "template": {"spec": {"containers": [{"name": name, "image": _image(name)}]}}
                },
            }
            for name in (
                "enterprise-doc-api",
                "enterprise-doc-worker",
                "enterprise-doc-consumer",
                "enterprise-doc-web",
            )
        ],
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "enterprise-doc-migrate"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "migrate",
                                "image": _image("enterprise-doc-api"),
                            }
                        ]
                    }
                }
            },
        },
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "enterprise-doc-embedding-rollout"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "embedding-rollout",
                                "image": _image("enterprise-doc-api"),
                            }
                        ]
                    }
                }
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
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "enterprise-doc-external-postgres-egress"},
            "spec": {
                "podSelector": {
                    "matchExpressions": [
                        {
                            "key": "app.kubernetes.io/name",
                            "operator": "In",
                            "values": [
                                "enterprise-doc-api",
                                "enterprise-doc-worker",
                                "enterprise-doc-consumer",
                                "enterprise-doc-migrate",
                                "enterprise-doc-embedding-rollout",
                            ],
                        },
                    ],
                },
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [{"ipBlock": {"cidr": "192.0.2.1/32"}}],
                        "ports": [
                            {"protocol": "TCP", "port": 5432},
                            {"protocol": "TCP", "port": 6543},
                        ],
                    },
                ],
            },
        },
    ]
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")


def _render_browser_manifest(tmp_path: Path, **overrides: str | None) -> list[dict]:
    source = tmp_path / "browser-template.yaml"
    destination = tmp_path / "browser-staging.yaml"
    if not source.exists():
        _write_template(source)
    configure_staging_manifest.configure_manifest(
        source,
        destination,
        staging_base_url="https://staging.example.com",
        object_store_endpoint="https://objects.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        web_object_store_origins="https://objects.example.com",
        database_egress_cidr="8.8.8.8/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        **overrides,
    )
    return [item for item in yaml.safe_load_all(destination.read_text(encoding="utf-8")) if item]


def test_configure_manifest_preserves_explicit_presales_route_and_wait_budget(
    tmp_path: Path,
) -> None:
    from enterprise_doc_core.presales.settings import PresalesSettings

    documents = _render_browser_manifest(
        tmp_path,
        presales_generation_enabled="true",
        presales_model_route="fallback",
        presales_model_timeout_seconds="120",
        presales_row_timeout_seconds="150",
        fallback_model_base_url="https://fallback.example.com/v1",
        fallback_model_name="presales-model",
    )
    data = next(item for item in documents if item["kind"] == "ConfigMap")["data"]
    assert {key: value for key, value in data.items() if key.startswith("PRESALES__")} == {
        "PRESALES__GENERATION_ENABLED": "true",
        "PRESALES__MODEL_ROUTE": "fallback",
        "PRESALES__MODEL_TIMEOUT_SECONDS": "120",
        "PRESALES__ROW_TIMEOUT_SECONDS": "150",
    }
    settings = PresalesSettings.model_validate(
        {
            key.removeprefix("PRESALES__").lower(): value
            for key, value in data.items()
            if key.startswith("PRESALES__")
        }
    )
    assert settings.generation_enabled and settings.model_route == "fallback"
    assert settings.model_timeout_seconds == 120 and settings.row_timeout_seconds == 150
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    config_hash = namespace["metadata"]["annotations"][
        "enterprise-doc-agent/approved-config-sha256"
    ]
    for item in documents:
        identity = (item["kind"], item["metadata"]["name"])
        if identity in configure_staging_manifest.CONFIG_CONSUMERS:
            assert (
                item["spec"]["template"]["metadata"]["annotations"][
                    "enterprise-doc-agent/config-sha256"
                ]
                == config_hash
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"presales_generation_enabled": "yes"},
        {"presales_generation_enabled": ""},
        {"presales_model_route": "automatic"},
        {"presales_model_route": "fallback"},
        {"presales_model_timeout_seconds": "NaN"},
        {"presales_model_timeout_seconds": "inf"},
        {"presales_model_timeout_seconds": "0"},
        {"presales_model_timeout_seconds": "181"},
        {"presales_model_timeout_seconds": "90"},
        {"presales_model_timeout_seconds": "not-a-number"},
        {"presales_row_timeout_seconds": "NaN"},
        {"presales_row_timeout_seconds": "inf"},
        {"presales_row_timeout_seconds": "0"},
        {"presales_row_timeout_seconds": "181"},
        {"presales_row_timeout_seconds": ""},
    ],
)
def test_invalid_presales_release_settings_fail_before_output(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="presales"):
        _render_browser_manifest(tmp_path, **overrides)
    assert not (tmp_path / "browser-staging.yaml").exists()


def test_presales_defaults_disable_generation_and_clear_old_model_override(tmp_path: Path) -> None:
    documents = _render_browser_manifest(
        tmp_path,
        presales_generation_enabled="true",
        presales_model_route="fallback",
        presales_model_timeout_seconds="120",
        presales_row_timeout_seconds="150",
        fallback_model_base_url="https://fallback.example.com/v1",
        fallback_model_name="presales-model",
    )
    enabled_annotations = next(item for item in documents if item["kind"] == "Namespace")[
        "metadata"
    ]["annotations"]
    (tmp_path / "browser-template.yaml").write_bytes(
        (tmp_path / "browser-staging.yaml").read_bytes()
    )
    disabled = _render_browser_manifest(tmp_path)
    data = next(item for item in disabled if item["kind"] == "ConfigMap")["data"]
    assert {key: value for key, value in data.items() if key.startswith("PRESALES__")} == {
        "PRESALES__GENERATION_ENABLED": "false",
        "PRESALES__MODEL_ROUTE": "primary",
        "PRESALES__ROW_TIMEOUT_SECONDS": "90",
    }
    disabled_annotations = next(item for item in disabled if item["kind"] == "Namespace")[
        "metadata"
    ]["annotations"]
    for key in (
        "enterprise-doc-agent/approved-config-sha256",
        "enterprise-doc-agent/prerequisites-sha256",
    ):
        assert disabled_annotations[key] != enabled_annotations[key]


def test_presales_cli_binds_approved_pilot_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "template.yaml"
    output = tmp_path / "rendered.yaml"
    _write_template(source)
    arguments = {
        "input": str(source),
        "output": str(output),
        "staging-base-url": "https://staging.example.com",
        "object-store-endpoint": "https://objects.example.com",
        "object-store-presign-endpoint": "https://objects.example.com",
        "tls-secret-name": "enterprise-doc-staging-tls",
        "web-object-store-origins": "https://objects.example.com",
        "database-egress-cidr": "8.8.8.8/32",
        "model-provider": "openai_compatible",
        "model-base-url": "https://model.example.com/v1",
        "model-name": "primary-model",
        "fallback-model-base-url": "https://fallback.example.com/v1",
        "fallback-model-name": "fallback-model",
        "embedding-base-url": "https://embedding.example.com/v1",
        "embedding-model-name": "embedding-model",
        "presales-generation-enabled": "true",
        "presales-model-route": "fallback",
        "presales-model-timeout-seconds": "120",
        "presales-row-timeout-seconds": "150",
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), *(arg for key, value in arguments.items() for arg in (f"--{key}", value))],
    )
    configure_staging_manifest.main()
    documents = list(yaml.safe_load_all(output.read_text(encoding="utf-8")))
    data = next(item for item in documents if item["kind"] == "ConfigMap")["data"]
    assert {key: value for key, value in data.items() if key.startswith("PRESALES__")} == {
        "PRESALES__GENERATION_ENABLED": "true",
        "PRESALES__MODEL_ROUTE": "fallback",
        "PRESALES__MODEL_TIMEOUT_SECONDS": "120",
        "PRESALES__ROW_TIMEOUT_SECONDS": "150",
    }


def test_staging_workflow_passes_presales_environment_to_the_renderer() -> None:
    workflow = yaml.safe_load(
        (SCRIPT.parents[1] / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    )
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "scripts/configure_staging_manifest.py" in step.get("run", "")
    )
    expected = {
        "DEMO_ENABLED": "${{ vars.STAGING_DEMO_ENABLED || 'false' }}",
        "PRESALES_GENERATION_ENABLED": "${{ vars.STAGING_PRESALES_GENERATION_ENABLED || 'false' }}",
        "PRESALES_MODEL_ROUTE": "${{ vars.STAGING_PRESALES_MODEL_ROUTE || 'primary' }}",
        "PRESALES_MODEL_TIMEOUT_SECONDS": "${{ vars.STAGING_PRESALES_MODEL_TIMEOUT_SECONDS }}",
        "PRESALES_ROW_TIMEOUT_SECONDS": "${{ vars.STAGING_PRESALES_ROW_TIMEOUT_SECONDS || '90' }}",
    }
    for name, expression in expected.items():
        assert step["env"].get(name) == expression
        assert f'--{name.lower().replace("_", "-")} "${name}"' in step["run"]
    assert len(workflow[True]["workflow_dispatch"]["inputs"]) == 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"demo_enabled": "yes"},
        {"demo_enabled": "true"},
        {"demo_enabled": "true", "presales_generation_enabled": "true"},
        {
            "demo_enabled": "true",
            "browser_auth_issuer": "https://auth.example.com/realms/docagent",
            "browser_auth_client_id": "docagent-web",
        },
    ],
)
def test_demo_release_requires_browser_sessions_and_generation(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="demo"):
        _render_browser_manifest(tmp_path, **overrides)
    assert not (tmp_path / "browser-staging.yaml").exists()


def test_demo_can_be_enabled_and_is_explicitly_disabled_by_default(tmp_path: Path) -> None:
    documents = _render_browser_manifest(
        tmp_path,
        demo_enabled="true",
        presales_generation_enabled="true",
        browser_auth_issuer="https://auth.example.com/realms/docagent",
        browser_auth_client_id="docagent-web",
    )
    assert (
        next(item for item in documents if item["kind"] == "ConfigMap")["data"]["DEMO__ENABLED"]
        == "true"
    )
    (tmp_path / "browser-template.yaml").write_bytes(
        (tmp_path / "browser-staging.yaml").read_bytes()
    )
    disabled = _render_browser_manifest(tmp_path)
    assert (
        next(item for item in disabled if item["kind"] == "ConfigMap")["data"]["DEMO__ENABLED"]
        == "false"
    )


def test_configure_manifest_binds_browser_login_to_application_settings(tmp_path: Path) -> None:
    from pydantic import SecretStr

    from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
    from enterprise_doc_core.config import AppEnvironment

    documents = _render_browser_manifest(
        tmp_path,
        browser_auth_issuer="https://auth.example.com/realms/docagent",
        browser_auth_client_id="docagent-web",
    )
    data = next(item for item in documents if item["kind"] == "ConfigMap")["data"]
    values = {
        key.removeprefix("BROWSER_AUTH__").lower(): value
        for key, value in data.items()
        if key.startswith("BROWSER_AUTH__")
    }
    values["algorithms"] = json.loads(values["algorithms"])
    settings = BrowserAuthSettings(**values, client_secret=SecretStr("test-only-secret"))
    settings.validate_environment(AppEnvironment.STAGING)
    assert settings.enabled
    assert settings.redirect_uri == "https://staging.example.com/auth/callback"
    assert settings.token_endpoint == (
        "https://auth.example.com/realms/docagent/protocol/openid-connect/token"
    )
    assert "BROWSER_AUTH__CLIENT_SECRET" not in data
    assert data.get("PRESALES__GENERATION_ENABLED", "false") == "false"
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    assert (
        namespace["metadata"]["annotations"]["enterprise-doc-agent/approved-browser-auth-issuer"]
        == settings.issuer
    )


def test_configure_manifest_github_uses_official_oauth_and_clears_stale_oidc(
    tmp_path: Path,
) -> None:
    from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
    from enterprise_doc_core.config import AppEnvironment

    _render_browser_manifest(
        tmp_path, browser_auth_issuer="https://auth.example.com/realms/docagent"
    )
    source = tmp_path / "browser-template.yaml"
    source.write_bytes((tmp_path / "browser-staging.yaml").read_bytes())
    documents = _render_browser_manifest(
        tmp_path,
        browser_auth_provider="github",
        browser_auth_issuer="https://github.com",
        browser_auth_client_id="github-client",
    )
    data = next(item for item in documents if item["kind"] == "ConfigMap")["data"]
    values = {
        key.removeprefix("BROWSER_AUTH__").lower(): value
        for key, value in data.items()
        if key.startswith("BROWSER_AUTH__")
    }
    settings = BrowserAuthSettings(**values, client_secret="test-only-secret")
    settings.validate_environment(AppEnvironment.STAGING)
    assert settings.provider == "github"
    assert settings.issuer == "https://github.com"
    assert settings.authorization_endpoint == "https://github.com/login/oauth/authorize"
    assert settings.token_endpoint == "https://github.com/login/oauth/access_token"
    assert "BROWSER_AUTH__JWKS_URL" not in data
    assert "BROWSER_AUTH__ALGORITHMS" not in data
    assert "BROWSER_AUTH__CLIENT_SECRET" not in data
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    assert (
        namespace["metadata"]["annotations"]["enterprise-doc-agent/approved-browser-auth-provider"]
        == "github"
    )
    source.write_bytes((tmp_path / "browser-staging.yaml").read_bytes())
    disabled = _render_browser_manifest(tmp_path)
    data = next(item for item in disabled if item["kind"] == "ConfigMap")["data"]
    assert {k: v for k, v in data.items() if k.startswith("BROWSER_AUTH__")} == {
        "BROWSER_AUTH__ENABLED": "false"
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"browser_auth_issuer": None},
        {"browser_auth_issuer": "https://github.com/"},
        {"browser_auth_client_id": None},
        {"browser_auth_oidc_config": "{}"},
        {"browser_auth_provider": "unknown"},
    ],
)
def test_github_manifest_rejects_incomplete_or_mixed_provider_settings(
    tmp_path: Path, overrides: dict
) -> None:
    config = {
        "browser_auth_provider": "github",
        "browser_auth_issuer": "https://github.com",
        "browser_auth_client_id": "github-client",
    } | overrides
    with pytest.raises(ValueError):
        _render_browser_manifest(tmp_path, **config)
    assert not (tmp_path / "browser-staging.yaml").exists()


@pytest.mark.parametrize(
    "issuer",
    [
        "http://auth.example.com/realms/docagent",
        "https://user:secret@auth.example.com/realms/docagent",
        "https://auth.example.com/realms/master",
        "https://auth.example.com/realms/docagent/",
        "https://auth.example.com/realms/docagent?token=private",
        "https://auth.example.com/realms/docagent;ignored",
        "https://auth.example.com/realms/%64ocagent",
        "https://staging.example.com/realms/docagent",
    ],
)
def test_browser_config_rejects_incompatible_issuer_before_output(
    tmp_path: Path, issuer: str
) -> None:
    with pytest.raises(ValueError):
        _render_browser_manifest(tmp_path, browser_auth_issuer=issuer)
    assert not (tmp_path / "browser-staging.yaml").exists()


@pytest.mark.parametrize(
    "issuer,authorization,token,jwks,algorithm",
    [
        (
            "https://team.cloudflareaccess.com/cdn-cgi/access/sso/oidc/client-id",
            "/authorization",
            "/token",
            "/jwks",
            "RS256",
        ),
        (
            "https://project.supabase.co/auth/v1",
            "/oauth/authorize",
            "/oauth/token",
            "/.well-known/jwks.json",
            "ES256",
        ),
    ],
    ids=["cloudflare-access", "supabase"],
)
def test_manifest_accepts_explicit_hosted_oidc_contract(
    tmp_path: Path, issuer: str, authorization: str, token: str, jwks: str, algorithm: str
) -> None:
    from pydantic import SecretStr

    from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
    from enterprise_doc_core.config import AppEnvironment

    documents = _render_browser_manifest(
        tmp_path,
        browser_auth_issuer=issuer,
        browser_auth_client_id="configured-client-id",
        browser_auth_oidc_config=json.dumps(
            {
                "authorization_endpoint": issuer + authorization,
                "token_endpoint": issuer + token,
                "jwks_uri": issuer + jwks,
                "algorithms": [algorithm],
            }
        ),
    )
    data = next(item for item in documents if item["kind"] == "ConfigMap")["data"]
    values = {
        key.removeprefix("BROWSER_AUTH__").lower(): value
        for key, value in data.items()
        if key.startswith("BROWSER_AUTH__")
    }
    values["algorithms"] = json.loads(values["algorithms"])
    settings = BrowserAuthSettings(**values, client_secret=SecretStr("test-only-secret"))
    settings.validate_environment(AppEnvironment.STAGING)
    assert settings.issuer == issuer
    assert settings.authorization_endpoint == issuer + authorization
    assert settings.token_endpoint == issuer + token
    assert settings.jwks_url == issuer + jwks
    assert settings.algorithms == (algorithm,)
    assert settings.redirect_uri == "https://staging.example.com/auth/callback"
    assert "BROWSER_AUTH__CLIENT_SECRET" not in data


@pytest.mark.parametrize(
    "changes",
    [
        {"algorithms": ["HS256"]},
        {"algorithms": []},
        {"algorithms": "ES256"},
        {"algorithms": ["ES256", "ES256"]},
        {"client_secret": "must-not-echo"},
        {"token_endpoint": "https://other.example.com/token"},
        {"token_endpoint": "http://project.supabase.co/token"},
        {"token_endpoint": "https://project.supabase.co/token?secret=must-not-echo"},
        {"token_endpoint": "https://user:must-not-echo@project.supabase.co/token"},
        {"token_endpoint": None},
    ],
)
def test_hosted_oidc_configuration_rejects_unsafe_or_incomplete_profile(
    tmp_path: Path, changes: dict
) -> None:
    issuer = "https://project.supabase.co/auth/v1"
    config = {
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "jwks_uri": issuer + "/.well-known/jwks.json",
        "algorithms": ["ES256"],
        **changes,
    }
    with pytest.raises(ValueError) as caught:
        _render_browser_manifest(
            tmp_path,
            browser_auth_issuer=issuer,
            browser_auth_client_id="configured-client-id",
            browser_auth_oidc_config=json.dumps(config),
        )
    assert not (tmp_path / "browser-staging.yaml").exists()
    assert "must-not-echo" not in str(caught.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"browser_auth_oidc_config": "{}"},
        {
            "browser_auth_issuer": "https://project.supabase.co/auth/v1",
            "browser_auth_oidc_config": "{}",
        },
        {
            "browser_auth_issuer": "https://project.supabase.co/auth/v1",
            "browser_auth_client_id": "configured-client-id",
            "browser_auth_oidc_config": "{}",
        },
        {
            "browser_auth_issuer": "https://project.supabase.co/auth/v1",
            "browser_auth_client_id": "configured-client-id",
            "browser_auth_oidc_config": "must-not-echo-invalid-json",
        },
    ],
)
def test_hosted_oidc_configuration_requires_explicit_issuer_client_and_endpoints(
    tmp_path: Path, overrides: dict
) -> None:
    with pytest.raises(ValueError) as caught:
        _render_browser_manifest(tmp_path, **overrides)
    assert not (tmp_path / "browser-staging.yaml").exists()
    assert "must-not-echo" not in str(caught.value)


def test_browser_config_disable_clears_old_endpoints_and_changes_restart_hash(
    tmp_path: Path,
) -> None:
    enabled = _render_browser_manifest(
        tmp_path, browser_auth_issuer="https://auth.example.com/realms/docagent"
    )
    (tmp_path / "browser-template.yaml").write_text(yaml.safe_dump_all(enabled), encoding="utf-8")
    disabled = _render_browser_manifest(tmp_path)
    config = next(item for item in disabled if item["kind"] == "ConfigMap")["data"]
    assert {key: value for key, value in config.items() if key.startswith("BROWSER_AUTH__")} == {
        "BROWSER_AUTH__ENABLED": "false"
    }
    annotations = [
        next(item for item in docs if item["kind"] == "Namespace")["metadata"]["annotations"]
        for docs in (enabled, disabled)
    ]
    assert (
        annotations[0]["enterprise-doc-agent/approved-config-sha256"]
        != annotations[1]["enterprise-doc-agent/approved-config-sha256"]
    )
    assert "enterprise-doc-agent/approved-browser-auth-issuer" not in annotations[1]


def test_browser_config_rejects_plaintext_secret_without_echoing_it(tmp_path: Path) -> None:
    source = tmp_path / "browser-template.yaml"
    _write_template(source)
    documents = list(yaml.safe_load_all(source.read_text()))
    next(item for item in documents if item["kind"] == "ConfigMap")["data"][
        "BROWSER_AUTH__CLIENT_SECRET"
    ] = "never-echo-this-test-value"
    source.write_text(yaml.safe_dump_all(documents), encoding="utf-8")
    with pytest.raises(ValueError, match="client secret") as caught:
        _render_browser_manifest(tmp_path)
    assert "never-echo-this-test-value" not in str(caught.value)
    assert not (tmp_path / "browser-staging.yaml").exists()


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
        database_egress_cidr="1.1.1.1/32,8.8.8.8/32,1.1.1.1/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        embedding_base_url="https://embedding.example.com/v1",
        embedding_model_name="Qwen/Qwen3-Embedding-4B",
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
    assert config["data"]["OBJECT_STORE__MULTIPART_CHECKSUM_MODE"] == "native_sha256"
    assert config["data"]["MODEL__PROVIDER"] == "openai_compatible"
    assert config["data"]["MODEL__BASE_URL"] == "https://model.example.com/v1"
    assert config["data"]["MODEL__MODEL_NAME"] == "staging-model"
    assert config["data"]["EMBEDDING__PROVIDER"] == "openai_compatible"
    assert config["data"]["EMBEDDING__BASE_URL"] == "https://embedding.example.com/v1"
    assert config["data"]["EMBEDDING__MODEL_NAME"] == "Qwen/Qwen3-Embedding-4B"
    assert config["data"]["EMBEDDING__DIMENSION"] == "1024"
    assert config["data"]["EMBEDDING__SEND_DIMENSIONS"] == "true"
    assert config["data"]["EMBEDDING__QUERY_INSTRUCTION"].startswith("Given a user question")
    assert config["data"]["EMBEDDING__VERSION"] == "2"
    assert config["data"]["RETRIEVAL__REQUIRE_VECTOR_EVIDENCE"] == "true"
    api_deployment = next(
        item
        for item in documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api"
    )
    for name in ("enterprise-doc-api", "enterprise-doc-worker", "enterprise-doc-consumer"):
        deployment = next(
            item
            for item in documents
            if item["kind"] == "Deployment" and item["metadata"]["name"] == name
        )
        assert re.fullmatch(
            r"[0-9a-f]{64}",
            deployment["spec"]["template"]["metadata"]["annotations"][
                "enterprise-doc-agent/config-sha256"
            ],
        )
    migration = next(
        item
        for item in documents
        if item["kind"] == "Job" and item["metadata"]["name"] == "enterprise-doc-migrate"
    )
    assert (
        migration["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
        == api_deployment["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
    )
    web = next(
        item
        for item in documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-web"
    )
    assert "annotations" not in web["spec"]["template"].get("metadata", {})
    ingress = next(item for item in documents if item["kind"] == "Ingress")
    assert ingress["spec"]["rules"][0]["host"] == "staging.example.com"
    assert ingress["spec"]["tls"] == [
        {
            "hosts": ["staging.example.com"],
            "secretName": "enterprise-doc-staging-tls",
        }
    ]
    assert all(item["kind"] != "Secret" for item in documents)
    database_policy = next(
        item
        for item in documents
        if item["kind"] == "NetworkPolicy"
        and item["metadata"]["name"] == "enterprise-doc-external-postgres-egress"
    )
    assert database_policy["spec"]["egress"] == [
        {
            "to": [
                {"ipBlock": {"cidr": "1.1.1.1/32"}},
                {"ipBlock": {"cidr": "8.8.8.8/32"}},
            ],
            "ports": [
                {"protocol": "TCP", "port": 5432},
                {"protocol": "TCP", "port": 6543},
            ],
        },
    ]


def test_configure_manifest_records_admin_owned_prerequisite_approval(
    tmp_path: Path,
) -> None:
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
        database_egress_cidr="8.8.8.8/32,1.1.1.1/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        rollback_api_image=_image("enterprise-doc-api", "1" * 64),
        rollback_worker_image=_image("enterprise-doc-worker", "2" * 64),
        rollback_consumer_image=_image("enterprise-doc-consumer", "3" * 64),
        rollback_web_image=_image("enterprise-doc-web", "4" * 64),
    )

    documents = [item for item in yaml.safe_load_all(destination.read_text()) if item]
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    annotations = namespace["metadata"]["annotations"]
    assert annotations["enterprise-doc-agent/approved-object-store-endpoint"] == (
        "https://objects.internal.example.com"
    )
    assert annotations["enterprise-doc-agent/approved-model-base-url"] == (
        "https://model.example.com/v1"
    )
    assert annotations["enterprise-doc-agent/approved-api-images"] == ",".join(
        [_image("enterprise-doc-api"), _image("enterprise-doc-api", "1" * 64)]
    )
    assert annotations["enterprise-doc-agent/approved-worker-images"] == ",".join(
        [_image("enterprise-doc-worker"), _image("enterprise-doc-worker", "2" * 64)]
    )
    assert annotations["enterprise-doc-agent/approved-consumer-images"] == ",".join(
        [_image("enterprise-doc-consumer"), _image("enterprise-doc-consumer", "3" * 64)]
    )
    assert annotations["enterprise-doc-agent/approved-web-images"] == ",".join(
        [_image("enterprise-doc-web"), _image("enterprise-doc-web", "4" * 64)]
    )
    assert annotations["enterprise-doc-agent/approved-prometheus-images"] == (
        configure_staging_manifest.PROMETHEUS_IMAGE
    )
    assert annotations["enterprise-doc-agent/approved-database-egress-cidr"] == (
        "1.1.1.1/32,8.8.8.8/32"
    )
    assert annotations["enterprise-doc-agent/approved-staging-host"] == "staging.example.com"
    assert annotations["enterprise-doc-agent/approved-tls-secret-name"] == (
        "enterprise-doc-staging-tls"
    )
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        annotations["enterprise-doc-agent/prerequisites-sha256"],
    )


def test_configure_manifest_hashes_reviewed_prometheus_config(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "staging.yaml"
    _write_template(source)
    documents = [item for item in yaml.safe_load_all(source.read_text()) if item]
    documents.extend(
        [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "enterprise-doc-prometheus-config"},
                "data": {"prometheus.yml": "global:\n  scrape_interval: 15s\n"},
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "enterprise-doc-prometheus"},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "prometheus",
                                    "image": configure_staging_manifest.PROMETHEUS_IMAGE,
                                }
                            ]
                        }
                    }
                },
            },
        ]
    )
    source.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")

    configure_staging_manifest.configure_manifest(
        source,
        destination,
        staging_base_url="https://staging.example.com",
        object_store_endpoint="https://objects.internal.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        web_object_store_origins="https://objects.example.com",
        database_egress_cidr="8.8.8.8/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
    )

    configured = [item for item in yaml.safe_load_all(destination.read_text()) if item]
    prometheus = next(
        item
        for item in configured
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-prometheus"
    )
    config_hash = prometheus["spec"]["template"]["metadata"]["annotations"][
        "enterprise-doc-agent/prometheus-config-sha256"
    ]
    assert re.fullmatch(r"[0-9a-f]{64}", config_hash)


@pytest.mark.parametrize(
    "rollback_image",
    [
        "registry.example.com/enterprise-doc-api:latest",
        "registry.example.com/other@sha256:" + "1" * 64,
    ],
)
def test_configure_manifest_rejects_unreviewed_rollback_image(
    tmp_path: Path,
    rollback_image: str,
) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="rollback image"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "invalid-rollback.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com",
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
            rollback_api_image=rollback_image,
        )


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
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
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
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
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
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
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
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
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
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
        )


@pytest.mark.parametrize(
    "cidrs",
    [
        "",
        "8.8.8.0/24",
        "10.0.0.1/32",
        "127.0.0.1/32",
        "::1/128",
        "not-a-cidr",
        "8.8.8.8/32,10.0.0.1/32",
    ],
)
def test_configure_manifest_rejects_non_global_single_host_database_egress(
    tmp_path: Path, cidrs: str
) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="database egress CIDRs"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "invalid-database-egress.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com",
            database_egress_cidr=cidrs,
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
        )


def _configure_model(
    source: Path,
    destination: Path,
    *,
    model_provider: str = "openai_compatible",
    model_base_url: str = "https://model.example.com/v1",
    model_name: str = "staging-model",
    fallback_model_base_url: str | None = None,
    fallback_model_name: str | None = None,
    fallback_model_version: str | None = None,
    fallback_model_timeout_seconds: str | None = None,
    embedding_version: str = "2",
) -> None:
    configure_staging_manifest.configure_manifest(
        source,
        destination,
        staging_base_url="https://staging.example.com",
        object_store_endpoint="https://objects.internal.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        web_object_store_origins="https://objects.example.com",
        database_egress_cidr="8.8.8.8/32",
        model_provider=model_provider,
        model_base_url=model_base_url,
        model_name=model_name,
        fallback_model_base_url=fallback_model_base_url,
        fallback_model_name=fallback_model_name,
        fallback_model_version=fallback_model_version,
        fallback_model_timeout_seconds=fallback_model_timeout_seconds,
        embedding_version=embedding_version,
    )


def test_configure_manifest_binds_optional_fallback_route_without_secret_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "fallback.yaml"
    primary_only_destination = tmp_path / "primary-only.yaml"
    _write_template(source)

    _configure_model(source, primary_only_destination)
    _configure_model(
        source,
        destination,
        fallback_model_base_url="https://fallback.example.com/v1",
        fallback_model_name="fallback-model",
        fallback_model_version="2026-08-17",
        fallback_model_timeout_seconds="60.0",
    )

    documents = [
        item
        for item in yaml.safe_load_all(destination.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]
    config = next(item for item in documents if item["kind"] == "ConfigMap")
    assert config["data"]["MODEL__FALLBACK_PROVIDER"] == "openai_compatible"
    assert config["data"]["MODEL__FALLBACK_BASE_URL"] == ("https://fallback.example.com/v1")
    assert config["data"]["MODEL__FALLBACK_MODEL_NAME"] == "fallback-model"
    assert config["data"]["MODEL__FALLBACK_MODEL_VERSION"] == "2026-08-17"
    assert config["data"]["MODEL__FALLBACK_TIMEOUT_SECONDS"] == "60"
    assert "MODEL__FALLBACK_API_KEY" not in config["data"]
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    annotations = namespace["metadata"]["annotations"]
    assert annotations["enterprise-doc-agent/approved-model-fallback-base-url"] == (
        "https://fallback.example.com/v1"
    )
    assert annotations["enterprise-doc-agent/approved-model-fallback-name"] == ("fallback-model")
    assert annotations["enterprise-doc-agent/approved-model-fallback-version"] == "2026-08-17"
    assert annotations["enterprise-doc-agent/approved-model-fallback-timeout-seconds"] == "60"
    assert annotations["enterprise-doc-agent/approved-model-fallback-secret-key"] == (
        "MODEL__FALLBACK_API_KEY"
    )
    primary_only_documents = [
        item
        for item in yaml.safe_load_all(primary_only_destination.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]
    primary_only_api = next(
        item
        for item in primary_only_documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api"
    )
    fallback_api = next(
        item
        for item in documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api"
    )
    assert (
        primary_only_api["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
        != fallback_api["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
    )


def test_configure_manifest_omits_fallback_route_when_unconfigured(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "primary-only.yaml"
    _write_template(source)
    source_documents = [item for item in yaml.safe_load_all(source.read_text()) if item]
    source_config = next(item for item in source_documents if item["kind"] == "ConfigMap")
    source_config["data"].update(
        {
            "MODEL__FALLBACK_MODEL_VERSION": "stale",
            "MODEL__FALLBACK_TIMEOUT_SECONDS": "99",
        }
    )
    source_namespace = next(item for item in source_documents if item["kind"] == "Namespace")
    source_namespace["metadata"]["annotations"].update(
        {
            "enterprise-doc-agent/approved-model-fallback-version": "stale",
            "enterprise-doc-agent/approved-model-fallback-timeout-seconds": "99",
            "enterprise-doc-agent/approved-model-fallback-secret-key": ("MODEL__FALLBACK_API_KEY"),
        }
    )
    source.write_text(yaml.safe_dump_all(source_documents, sort_keys=False), encoding="utf-8")

    _configure_model(source, destination)

    documents = [
        item
        for item in yaml.safe_load_all(destination.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]
    config = next(item for item in documents if item["kind"] == "ConfigMap")
    assert not {key for key in config["data"] if key.startswith("MODEL__FALLBACK_")}
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    assert not {
        key
        for key in namespace["metadata"]["annotations"]
        if key.startswith("enterprise-doc-agent/approved-model-fallback-")
    }


def test_configure_manifest_binds_embedding_generation_version(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "embedding-v3.yaml"
    _write_template(source)

    _configure_model(source, destination, embedding_version="3")

    documents = [item for item in yaml.safe_load_all(destination.read_text()) if item]
    config = next(item for item in documents if item["kind"] == "ConfigMap")
    namespace = next(item for item in documents if item["kind"] == "Namespace")
    assert config["data"]["EMBEDDING__VERSION"] == "3"
    assert (
        namespace["metadata"]["annotations"]["enterprise-doc-agent/approved-embedding-version"]
        == "3"
    )


@pytest.mark.parametrize("value", ["0", "1001", "version-3", " "])
def test_configure_manifest_rejects_invalid_embedding_generation_version(
    tmp_path: Path, value: str
) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="embedding version"):
        _configure_model(source, tmp_path / "invalid-version.yaml", embedding_version=value)


@pytest.mark.parametrize(
    ("fallback_model_base_url", "fallback_model_name"),
    [
        ("https://fallback.example.com/v1", None),
        (None, "fallback-model"),
    ],
)
def test_configure_manifest_rejects_partial_fallback_route(
    tmp_path: Path,
    fallback_model_base_url: str | None,
    fallback_model_name: str | None,
) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="fallback model route"):
        _configure_model(
            source,
            tmp_path / "partial-fallback.yaml",
            fallback_model_base_url=fallback_model_base_url,
            fallback_model_name=fallback_model_name,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("fallback_model_version", "v\x00", "model version"),
        ("fallback_model_version", "v" * 101, "model version"),
        ("fallback_model_timeout_seconds", "0", "model timeout"),
        ("fallback_model_timeout_seconds", "301", "model timeout"),
        ("fallback_model_timeout_seconds", "not-a-number", "model timeout"),
    ],
)
def test_configure_manifest_rejects_invalid_fallback_metadata(
    tmp_path: Path,
    field: str,
    value: str,
    match: str,
) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match=match):
        _configure_model(
            source,
            tmp_path / "invalid-fallback-metadata.yaml",
            fallback_model_base_url="https://fallback.example.com/v1",
            fallback_model_name="fallback-model",
            **{field: value},
        )


def test_configure_manifest_rejects_fallback_metadata_without_route(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="require a configured fallback route"):
        _configure_model(
            source,
            tmp_path / "metadata-without-route.yaml",
            fallback_model_version="2026-08-17",
        )


def test_configure_manifest_rejects_unreviewed_model_provider(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="model provider"):
        _configure_model(
            source,
            tmp_path / "bad-provider.yaml",
            model_provider="deterministic",
        )


@pytest.mark.parametrize(
    "value",
    [
        "http://model.example.com/v1",
        "https://user:password@model.example.com/v1",
        "https://model.example.com/v1?key=value",
        "https://model.example.com/v1#fragment",
        "https://localhost/v1",
        "https://api.localhost/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.1/v1",
        "https://169.254.169.254/v1",
        "https://192.0.2.1/v1",
        "https://[fe80::1]/v1",
        "https://model.example.com:not-a-port/v1",
        "https://model.example.com/api",
        "https://model.example.com/v1\nignored",
    ],
)
def test_configure_manifest_rejects_unsafe_model_base_url(tmp_path: Path, value: str) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="model base URL"):
        _configure_model(
            source,
            tmp_path / "bad-model-url.yaml",
            model_base_url=value,
        )


@pytest.mark.parametrize("value", [" ", "bad\nname", "x" * 201])
def test_configure_manifest_rejects_invalid_model_name(tmp_path: Path, value: str) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="model name"):
        _configure_model(
            source,
            tmp_path / "bad-model-name.yaml",
            model_name=value,
        )


def test_configure_manifest_normalizes_model_values_and_rotates_config_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "template.yaml"
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    _write_template(source)

    _configure_model(
        source,
        first,
        model_base_url=" https://model.example.com/v1/ ",
        model_name=" " + "x" * 200 + " ",
    )
    _configure_model(source, second, model_name="different-model")

    first_documents = [item for item in yaml.safe_load_all(first.read_text()) if item]
    second_documents = [item for item in yaml.safe_load_all(second.read_text()) if item]
    first_config = next(item for item in first_documents if item["kind"] == "ConfigMap")
    assert first_config["data"]["MODEL__BASE_URL"] == "https://model.example.com/v1"
    assert first_config["data"]["MODEL__MODEL_NAME"] == "x" * 200
    first_api = next(
        item
        for item in first_documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api"
    )
    second_api = next(
        item
        for item in second_documents
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "enterprise-doc-api"
    )
    assert (
        first_api["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
        != second_api["spec"]["template"]["metadata"]["annotations"][
            "enterprise-doc-agent/config-sha256"
        ]
    )


def test_configure_manifest_supports_r2_readback_checksum_mode(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    destination = tmp_path / "readback.yaml"
    _write_template(source)

    configure_staging_manifest.configure_manifest(
        source,
        destination,
        staging_base_url="https://staging.example.com",
        object_store_endpoint="https://objects.internal.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="enterprise-doc-staging-tls",
        web_object_store_origins="https://objects.example.com",
        database_egress_cidr="8.8.8.8/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="staging-model",
        object_store_checksum_mode="readback_sha256",
    )

    documents = [item for item in yaml.safe_load_all(destination.read_text()) if item]
    config = next(item for item in documents if item["kind"] == "ConfigMap")
    assert config["data"]["OBJECT_STORE__MULTIPART_CHECKSUM_MODE"] == "readback_sha256"


def test_configure_manifest_rejects_unknown_checksum_mode(tmp_path: Path) -> None:
    source = tmp_path / "template.yaml"
    _write_template(source)

    with pytest.raises(ValueError, match="checksum mode"):
        configure_staging_manifest.configure_manifest(
            source,
            tmp_path / "invalid.yaml",
            staging_base_url="https://staging.example.com",
            object_store_endpoint="https://objects.internal.example.com",
            object_store_presign_endpoint="https://objects.example.com",
            tls_secret_name="enterprise-doc-staging-tls",
            web_object_store_origins="https://objects.example.com",
            database_egress_cidr="8.8.8.8/32",
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
            model_name="staging-model",
            object_store_checksum_mode="unsupported",
        )
