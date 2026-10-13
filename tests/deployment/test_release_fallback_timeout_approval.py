import copy

import pytest
from scripts.maintenance_guard import GuardError
from scripts.maintenance_guard_cluster import canonical_digest
from scripts.release_switch import ReleasePlan
from tests.deployment.test_release_fallback_credentials import KEY, cluster_for, fallback_plan
from tests.deployment.test_release_switch import PREFIX, Boundary

APPROVAL = PREFIX + "approved-model-fallback-timeout-seconds"


def timeout_plan():
    data, _, _ = fallback_plan()
    for phase, seconds in (("original", "60"), ("candidate", "180")):
        namespace, config = data[phase + "_prerequisites"]
        config["data"]["MODEL__FALLBACK_TIMEOUT_SECONDS"] = seconds
        digest = canonical_digest(config["data"])
        namespace["metadata"]["annotations"].update(
            {APPROVAL: seconds, PREFIX + "approved-config-sha256": digest}
        )
        for item in data["deployments" if phase == "original" else "candidate_deployments"]:
            item["spec"]["template"]["metadata"]["annotations"][PREFIX + "config-sha256"] = digest
    return data


def test_declared_fallback_timeout_and_approval_apply_and_restore_together():
    data = timeout_plan()
    boundary = Boundary(data)
    secret = next(item for item in boundary.items if item["kind"] == "Secret")
    secret["data"][KEY] = data["fallback_secret"]["old_key"]
    original_secret = copy.deepcopy(secret["data"])
    cluster = cluster_for(data, boundary)
    cluster.apply(100)
    namespace = next(item for item in boundary.items if item["kind"] == "Namespace")
    config = next(item for item in boundary.items if item["kind"] == "ConfigMap")
    assert namespace["metadata"]["annotations"][APPROVAL] == "180"
    assert config["data"]["MODEL__FALLBACK_TIMEOUT_SECONDS"] == "180"
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)
    assert namespace["metadata"]["annotations"][APPROVAL] == "60"
    assert config["data"] == data["original_prerequisites"][1]["data"]
    assert secret["data"] == original_secret


@pytest.mark.parametrize("phase", ["original", "candidate"])
@pytest.mark.parametrize("value", ["90", "180.0", "nan", "", None])
def test_timeout_approval_must_match_its_own_configuration(phase, value):
    data = timeout_plan()
    annotations = data[phase + "_prerequisites"][0]["metadata"]["annotations"]
    if value is None:
        del annotations[APPROVAL]
    else:
        annotations[APPROVAL] = value
    with pytest.raises(GuardError):
        ReleasePlan(data)


def test_unrelated_fallback_version_approval_stays_outside_scope():
    data = timeout_plan()
    key = PREFIX + "approved-model-fallback-version"
    data["original_prerequisites"][0]["metadata"]["annotations"][key] = "old"
    data["candidate_prerequisites"][0]["metadata"]["annotations"][key] = "new"
    with pytest.raises(GuardError, match="approval exceeds release scope"):
        ReleasePlan(data)
