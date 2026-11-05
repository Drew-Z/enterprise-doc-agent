import copy
from pathlib import Path

import pytest
import yaml
from scripts.release_switch import GuardError, ReleaseCluster, ReleasePlan
from tests.deployment.test_configure_staging_manifest import (
    _render_browser_manifest,
    _write_template,
)
from tests.deployment.test_release_switch import (
    PREFIX,
    Boundary,
    image_switch_data,
    refresh_inference_hashes,
)

KEY = "WORKER__PRESALES_CONCURRENCY"


def concurrency_data(remove=False):
    data = image_switch_data()
    data["release_kind"] = "presales_concurrency"
    data["original_prerequisites" if remove else "candidate_prerequisites"][1]["data"][KEY] = "2"
    refresh_inference_hashes(data)
    return data


@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_concurrency_release_applies_and_restores_exact_configuration_and_images(remove, partial):
    data = concurrency_data(remove)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261005_0032",
        idle=lambda timeout: True,
        clock=lambda: 1.0,
    )
    if partial:
        for item in boundary.items:
            if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "enterprise-doc-config":
                item["data"] = copy.deepcopy(data["candidate_prerequisites"][1]["data"])
    else:
        cluster.apply(100)
        cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)
    for original in data["original_prerequisites"] + data["deployments"]:
        current = next(
            i for i in boundary.items if i["metadata"]["name"] == original["metadata"]["name"]
        )
        for key in ("data", "spec"):
            if key in original:
                assert current[key] == original[key]


@pytest.mark.parametrize(
    "mutation",
    [
        "zero",
        "unbounded",
        "fraction",
        "bool",
        "pool",
        "replicas",
        "credential",
        "budget",
        "inference",
        "implicit",
        "schema",
        "approval",
        "noop",
    ],
)
def test_concurrency_release_rejects_unrelated_changes_with_valid_hashes(mutation):
    data = concurrency_data()
    config = data["candidate_prerequisites"][1]["data"]
    if mutation in {"zero", "unbounded", "fraction", "bool"}:
        config[KEY] = {"zero": "0", "unbounded": "5", "fraction": "1.5", "bool": True}[mutation]
    elif mutation == "pool":
        data["api_database_pool_size"] = 2
    elif mutation == "replicas":
        data["candidate_deployments"][1]["spec"]["replicas"] = 2
    elif mutation == "credential":
        data["secret"]["new_key"] = "bmV3"
    elif mutation == "budget":
        config["PRESALES__ROW_TIMEOUT_SECONDS"] = "900"
    elif mutation == "inference":
        config["PRESALES__PRIMARY_REASONING_EFFORT"] = "high"
    elif mutation == "implicit":
        data.pop("release_kind")
    elif mutation == "schema":
        data["original_revision"] = "20260924_0031"
    elif mutation == "approval":
        data["candidate_prerequisites"][0]["metadata"]["annotations"][
            PREFIX + "approved-model-base-url"
        ] = "https://changed.invalid/v1"
    else:
        config.pop(KEY)
    refresh_inference_hashes(data)
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("value", ["1", "2", "4"])
def test_renderer_binds_concurrency_to_approval_and_runtime_settings(tmp_path, value):
    documents = _render_browser_manifest(tmp_path, worker_presales_concurrency=value)
    config = next(d for d in documents if d["kind"] == "ConfigMap")["data"]
    namespace = next(d for d in documents if d["kind"] == "Namespace")
    from scripts.maintenance_guard_cluster import canonical_digest

    from enterprise_doc_worker.config import WorkerServerSettings

    assert config[KEY] == value
    assert namespace["metadata"]["annotations"][
        PREFIX + "approved-config-sha256"
    ] == canonical_digest(config)
    assert WorkerServerSettings(presales_concurrency=config[KEY]).presales_concurrency == int(value)


def test_renderer_removes_a_stale_concurrency_override(tmp_path):
    source = tmp_path / "browser-template.yaml"
    _write_template(source)
    documents = list(yaml.safe_load_all(source.read_text(encoding="utf-8")))
    next(d for d in documents if d["kind"] == "ConfigMap")["data"][KEY] = "4"
    source.write_text(yaml.safe_dump_all(documents), encoding="utf-8")
    rendered = _render_browser_manifest(tmp_path)
    assert KEY not in next(d for d in rendered if d["kind"] == "ConfigMap")["data"]


@pytest.mark.parametrize("value", ["0", "5", "1.5", "true"])
def test_renderer_rejects_invalid_concurrency_before_writing(tmp_path, value):
    with pytest.raises(ValueError, match="presales concurrency"):
        _render_browser_manifest(tmp_path, worker_presales_concurrency=value)
    assert not (tmp_path / "browser-staging.yaml").exists()


def test_deployment_workflow_passes_the_explicit_worker_setting():
    path = Path(__file__).parents[2] / ".github/workflows/deploy-staging.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    step = next(s for s in steps if s.get("name") == "Render and validate staging manifests")
    assert (
        step["env"]["WORKER_PRESALES_CONCURRENCY"]
        == "${{ vars.STAGING_WORKER_PRESALES_CONCURRENCY }}"
    )
    assert '--worker-presales-concurrency "$WORKER_PRESALES_CONCURRENCY"' in step["run"]
