import base64
import copy

import pytest
from scripts.maintenance_guard import GuardError
from scripts.maintenance_guard_cluster import canonical_digest
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_release_switch import PREFIX, Boundary, release_data

KEY = "MODEL__FALLBACK_API_KEY"


def fallback_plan():
    data = release_data()
    data["fallback_secret"] = {
        "old_key": base64.b64encode(b"old-fallback").decode(),
        "new_key": base64.b64encode(b"new-fallback").decode(),
    }
    for phase, host in (("original", "old"), ("candidate", "new")):
        namespace, config = data[phase + "_prerequisites"]
        config["data"].update(
            MODEL__FALLBACK_BASE_URL=f"https://{host}.invalid/v1",
            MODEL__FALLBACK_MODEL_NAME=f"{host}-fallback",
        )
        namespace["metadata"]["annotations"].update(
            {
                PREFIX + "approved-config-sha256": canonical_digest(config["data"]),
                PREFIX + "approved-model-fallback-base-url": config["data"][
                    "MODEL__FALLBACK_BASE_URL"
                ],
                PREFIX + "approved-model-fallback-name": config["data"][
                    "MODEL__FALLBACK_MODEL_NAME"
                ],
                PREFIX + "approved-model-fallback-provider": "openai_compatible",
                PREFIX + "approved-model-fallback-secret-key": KEY,
            }
        )
        deployments = data["deployments" if phase == "original" else "candidate_deployments"]
        for deployment in deployments:
            deployment["spec"]["template"]["metadata"]["annotations"][PREFIX + "config-sha256"] = (
                canonical_digest(config["data"])
            )
    boundary = Boundary(data)
    secret = next(item for item in boundary.items if item["kind"] == "Secret")
    secret["data"][KEY] = data["fallback_secret"]["old_key"]
    return data, boundary, secret


def cluster_for(data, boundary):
    return ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda _: "20260924_0031",
        clock=lambda: 1.0,
        idle=lambda _: True,
    )


def test_existing_fallback_endpoint_and_both_credentials_apply_and_restore():
    data, boundary, secret = fallback_plan()
    original = copy.deepcopy(secret["data"])
    cluster = cluster_for(data, boundary)
    cluster.apply(100)
    assert secret["data"] == {
        "MODEL__API_KEY": data["secret"]["new_key"],
        KEY: data["fallback_secret"]["new_key"],
        "DATABASE__URL": "unchanged",
    }
    cluster.verify(True, 100)
    cluster.restore(100)
    assert secret["data"] == original
    cluster.verify(False, 100)


def test_new_executor_restores_fallback_after_partial_bundle_write():
    data, boundary, secret = fallback_plan()
    cluster = cluster_for(data, boundary)
    cluster._close(100)
    boundary.fail = "enterprise-doc-api"
    with pytest.raises(TimeoutError):
        cluster._bundle(True, 100)
    assert secret["data"][KEY] == data["fallback_secret"]["new_key"]
    boundary.fail = None
    cluster_for(data, boundary).restore(100)
    assert secret["data"][KEY] == data["fallback_secret"]["old_key"]


@pytest.mark.parametrize("field", [KEY, "DATABASE__URL", "MODEL__API_KEY"])
def test_fallback_secret_or_unrelated_drift_blocks_before_writes(field):
    data, boundary, secret = fallback_plan()
    secret["data"][field] = "drift"
    with pytest.raises(GuardError):
        cluster_for(data, boundary).apply(100)
    assert boundary.writes == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"old_key": ""},
        {"old_key": "not-base64", "new_key": "bmV3"},
        {"old_key": "b2xk", "new_key": 42},
    ],
)
def test_invalid_explicit_fallback_key_binding_rejected(value):
    data, _, _ = fallback_plan()
    data["fallback_secret"] = value
    with pytest.raises(GuardError):
        ReleasePlan(data)


def test_changing_fallback_endpoint_requires_explicit_credential_binding():
    data, _, _ = fallback_plan()
    del data["fallback_secret"]
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("phase", ["original", "candidate"])
@pytest.mark.parametrize("suffix", ["base-url", "provider", "secret-key"])
def test_fallback_route_approval_mismatch_rejected(phase, suffix):
    data, _, _ = fallback_plan()
    data[phase + "_prerequisites"][0]["metadata"]["annotations"][
        PREFIX + "approved-model-fallback-" + suffix
    ] = "wrong"
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.invalid/v1",
        "https://user:password@provider.invalid/v1",
        "https://provider.invalid/v1?key=private",
        "https://provider.invalid/v1#fragment",
        "https://provider.invalid/v1\n",
        "https://provider.invalid:broken/v1",
        "https://provider.invalid:0/v1",
    ],
)
def test_fallback_endpoint_rejects_insecure_or_credential_bearing_urls(url):
    data, _, _ = fallback_plan()
    data["candidate_prerequisites"][1]["data"]["MODEL__FALLBACK_BASE_URL"] = url
    config = data["candidate_prerequisites"][1]["data"]
    annotations = data["candidate_prerequisites"][0]["metadata"]["annotations"]
    annotations[PREFIX + "approved-config-sha256"] = canonical_digest(config)
    annotations[PREFIX + "approved-model-fallback-base-url"] = url
    for item in data["candidate_deployments"]:
        item["spec"]["template"]["metadata"]["annotations"][PREFIX + "config-sha256"] = (
            canonical_digest(config)
        )
    with pytest.raises(GuardError, match="invalid fallback endpoint"):
        ReleasePlan(data)


def test_undeclared_fallback_key_remains_in_unrelated_secret_fingerprint():
    data = release_data()
    old_key = base64.b64encode(b"unchanged-fallback").decode()
    data["secret"]["other_data_sha256"] = canonical_digest(
        {"DATABASE__URL": "unchanged", KEY: old_key}
    )
    boundary = Boundary(data)
    secret = next(item for item in boundary.items if item["kind"] == "Secret")
    secret["data"][KEY] = old_key
    cluster_for(data, boundary).apply(100)
    assert secret["data"][KEY] == old_key
    secret["data"][KEY] = "drift"
    with pytest.raises(GuardError):
        cluster_for(data, boundary).restore(100)
