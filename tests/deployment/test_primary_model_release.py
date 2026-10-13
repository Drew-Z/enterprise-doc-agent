import copy

import pytest
from scripts.maintenance_guard import GuardError
from scripts.release_switch import PREFIX, ReleaseCluster, ReleasePlan
from tests.deployment.test_citation_release_switch import citation_switch_data
from tests.deployment.test_release_switch import Boundary, refresh_inference_hashes


def primary_model_data():
    data = citation_switch_data(original=True)
    data["release_kind"] = "primary_model"
    config = data["candidate_prerequisites"][1]["data"]
    config.update(
        {
            "MODEL__BASE_URL": "https://gpt.invalid/v1",
            "MODEL__MODEL_NAME": "gpt-6-luna",
            "PRESALES__PRIMARY_QUESTION_ASSESSMENT": "true",
        }
    )
    approvals = data["candidate_prerequisites"][0]["metadata"]["annotations"]
    approvals[PREFIX + "approved-model-base-url"] = config["MODEL__BASE_URL"]
    approvals[PREFIX + "approved-model-name"] = config["MODEL__MODEL_NAME"]
    data["secret"]["new_key"] = "bmV3LWdwdC1rZXk="
    refresh_inference_hashes(data)
    return data


@pytest.mark.parametrize("partial", [False, True])
def test_primary_model_switch_and_recovery_preserve_history_fallback_and_schema(partial):
    data = primary_model_data()
    plan = ReleasePlan(data)
    assert plan.primary_model
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        plan,
        run=boundary,
        revision=lambda _: "20261010_0037",
        idle=lambda _: True,
        workbook_empty=lambda _: False,
        manual_empty=lambda _: False,
        citation_empty=lambda _: False,
        clock=lambda: 1,
    )
    if partial:
        boundary.fail = "enterprise-doc-secrets"
        with pytest.raises(TimeoutError):
            cluster.apply(100)
        boundary.fail = None
    else:
        cluster.apply(100)
        cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize(
    "mutation",
    [
        "revision",
        "fallback_key",
        "fallback_model",
        "timeout",
        "pool",
        "unknown_mode",
        "bad_endpoint",
        "credential_url",
        "empty_model",
        "invalid_flag",
        "fallback_flag",
        "noop",
    ],
)
def test_primary_model_mode_refuses_changes_outside_exact_boundary(mutation):
    data = primary_model_data()
    config = data["candidate_prerequisites"][1]["data"]
    if mutation == "revision":
        data["original_revision"] = "20261009_0036"
    elif mutation == "fallback_key":
        data["fallback_secret"] = {"old_key": "b2xk", "new_key": "bmV3"}
    elif mutation == "fallback_model":
        config["MODEL__FALLBACK_MODEL_NAME"] = "other"
    elif mutation == "timeout":
        config["PRESALES__ROW_TIMEOUT_SECONDS"] = "900"
    elif mutation == "pool":
        data["api_database_pool_size"] = 2
    elif mutation == "unknown_mode":
        data["release_kind"] = "images_only"
    elif mutation == "bad_endpoint":
        config["MODEL__BASE_URL"] = "http://gpt.invalid/v1"
    elif mutation == "credential_url":
        config["MODEL__BASE_URL"] = "https://key@gpt.invalid/v1"
    elif mutation == "empty_model":
        config["MODEL__MODEL_NAME"] = " "
    elif mutation == "invalid_flag":
        config["PRESALES__PRIMARY_QUESTION_ASSESSMENT"] = "yes"
    elif mutation == "fallback_flag":
        config["PRESALES__FALLBACK_QUESTION_ASSESSMENT"] = "true"
    else:
        data["candidate_prerequisites"] = copy.deepcopy(data["original_prerequisites"])
        data["candidate_deployments"] = copy.deepcopy(data["deployments"])
        data["secret"]["new_key"] = data["secret"]["old_key"]
    if mutation in {"bad_endpoint", "credential_url", "empty_model"}:
        annotations = data["candidate_prerequisites"][0]["metadata"]["annotations"]
        annotations[PREFIX + "approved-model-base-url"] = config["MODEL__BASE_URL"]
        annotations[PREFIX + "approved-model-name"] = config["MODEL__MODEL_NAME"]
    refresh_inference_hashes(data)
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("action", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_primary_switch_cannot_hand_active_policies_to_changed_route(action, during_close):
    data = primary_model_data()
    boundary = Boundary(data)
    idle = [during_close]

    def run(args, payload, timeout):
        result = boundary(args, payload, timeout)
        if args[0] == "patch":
            idle[0] = False
        return result

    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=run,
        revision=lambda _: "20261010_0037",
        idle=lambda _: idle[0],
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        getattr(cluster, action)(100)
    assert not boundary.writes or all(kind == "deployment" for kind, _ in boundary.writes)
