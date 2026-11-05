import copy

import pytest
import yaml
from scripts.configure_staging_manifest import configure_manifest
from scripts.maintenance_guard import GuardError
from scripts.maintenance_guard_cluster import canonical_digest
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_configure_staging_manifest import _write_template
from tests.deployment.test_release_switch import PREFIX, Boundary, release_data


def render(source, destination, **values):
    configure_manifest(
        source,
        destination,
        staging_base_url="https://agent.example.com",
        object_store_endpoint="https://objects.example.com",
        object_store_presign_endpoint="https://objects.example.com",
        tls_secret_name="staging-tls",
        web_object_store_origins="https://objects.example.com",
        database_egress_cidr="8.8.8.8/32",
        model_provider="openai_compatible",
        model_base_url="https://model.example.com/v1",
        model_name="test-model",
        **values,
    )


@pytest.mark.parametrize(
    "primary,fallback", [("true", "false"), ("false", "true"), ("true", "true")]
)
def test_stream_rendering_and_reset(tmp_path, primary, fallback):
    source = tmp_path / "source.yaml"
    output = tmp_path / "output.yaml"
    reset = tmp_path / "reset.yaml"
    _write_template(source)
    render(
        source,
        output,
        model_streaming=primary,
        fallback_model_streaming=fallback,
        fallback_model_base_url="https://fallback.example.com/v1",
        fallback_model_name="fallback",
    )
    config = next(
        d["data"] for d in yaml.safe_load_all(output.read_text()) if d["kind"] == "ConfigMap"
    )
    assert config.get("MODEL__STREAMING", "false") == primary
    assert config.get("MODEL__FALLBACK_STREAMING", "false") == fallback
    render(output, reset)
    config = next(
        d["data"] for d in yaml.safe_load_all(reset.read_text()) if d["kind"] == "ConfigMap"
    )
    assert "MODEL__STREAMING" not in config and "MODEL__FALLBACK_STREAMING" not in config


@pytest.mark.parametrize("field", ["model_streaming", "fallback_model_streaming"])
@pytest.mark.parametrize("value", ["TRUE", "yes", " true", True])
def test_invalid_stream_config_is_rejected_before_writes(tmp_path, field, value):
    source = tmp_path / "source.yaml"
    output = tmp_path / "output.yaml"
    _write_template(source)
    with pytest.raises(ValueError, match="streaming"):
        render(source, output, **{field: value})
    assert not output.exists()


@pytest.mark.parametrize("key", ["MODEL__STREAMING", "MODEL__FALLBACK_STREAMING"])
@pytest.mark.parametrize("value", [None, "true", "false", "invalid"])
def test_stream_switch_binds_config_and_restores_original(key, value):
    data = release_data()
    data["original_prerequisites"][1]["data"][key] = "true"
    if value is not None:
        data["candidate_prerequisites"][1]["data"][key] = value
    for prerequisites, deployments in (
        (data["original_prerequisites"], data["deployments"]),
        (data["candidate_prerequisites"], data["candidate_deployments"]),
    ):
        digest = canonical_digest(prerequisites[1]["data"])
        prerequisites[0]["metadata"]["annotations"][PREFIX + "approved-config-sha256"] = digest
        for deployment in deployments:
            deployment["spec"]["template"]["metadata"]["annotations"][PREFIX + "config-sha256"] = (
                digest
            )
    if value == "invalid":
        with pytest.raises(GuardError):
            ReleasePlan(data)
        return
    drift = copy.deepcopy(data)
    drift["candidate_prerequisites"][1]["data"][key] = "false" if value == "true" else "true"
    with pytest.raises(GuardError, match="fingerprint"):
        ReleasePlan(drift)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda _: "20260924_0031",
        idle=lambda _: True,
        clock=lambda: 1.0,
    )
    cluster.apply(100)
    assert next(x["data"] for x in boundary.items if x["kind"] == "ConfigMap").get(key) == value
    cluster.restore(100)
    assert next(x["data"] for x in boundary.items if x["kind"] == "ConfigMap")[key] == "true"
