import copy

import pytest
from scripts.maintenance_guard import GuardError
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_citation_release_switch import citation_switch_data
from tests.deployment.test_release_switch import Boundary, refresh_inference_hashes


@pytest.mark.parametrize(
    "readers", [None, {}, {"original": True}, {"original": 1, "candidate": True}]
)
def test_0037_requires_explicit_missing_information_reader_capabilities(readers):
    data = citation_switch_data(original=True)
    data.pop("missing_information_readers", None)
    if readers is not None:
        data["missing_information_readers"] = readers
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("action", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_extended_history_blocks_legacy_reader_before_and_after_closing(action, during_close):
    data = citation_switch_data(original=True)
    data["missing_information_readers"] = {"original": False, "candidate": True}
    boundary = Boundary(data)
    empty = [during_close]

    def run(args, payload, timeout):
        result = boundary(args, payload, timeout)
        if args[0] == "patch":
            empty[0] = False
        return result

    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=run,
        revision=lambda _: "20261010_0037",
        idle=lambda _: True,
        missing_information_empty=lambda _: empty[0],
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        getattr(cluster, action)(100)
    assert not boundary.writes or all(kind == "deployment" for kind, _ in boundary.writes)
    if during_close:
        for original in data["deployments"]:
            current = next(
                i for i in boundary.items if i["metadata"]["name"] == original["metadata"]["name"]
            )
            assert current["spec"] == original["spec"] | {"replicas": 0}


def test_prompt_activation_and_compatible_rollback_keep_images_credentials_and_history():
    data = citation_switch_data(original=True)
    data["release_kind"] = "primary_model"
    data["missing_information_readers"] = {"original": True, "candidate": True}
    data["candidate_deployments"] = copy.deepcopy(data["deployments"])
    data["candidate_prerequisites"] = copy.deepcopy(data["original_prerequisites"])
    data["original_prerequisites"][1]["data"]["PRESALES__QUESTION_PROMPT_VERSION"] = "presales.v21"
    data["candidate_prerequisites"][1]["data"]["PRESALES__QUESTION_PROMPT_VERSION"] = "presales.v24"
    refresh_inference_hashes(data)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda _: "20261010_0037",
        idle=lambda _: True,
        missing_information_empty=lambda _: False,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize("version", ["presales.v22", "presales.v23", "", "unknown"])
def test_release_rejects_unreviewed_prompt_versions(version):
    data = citation_switch_data(original=True)
    data["release_kind"] = "primary_model"
    data["candidate_prerequisites"][1]["data"]["PRESALES__QUESTION_PROMPT_VERSION"] = version
    refresh_inference_hashes(data)
    with pytest.raises(GuardError):
        ReleasePlan(data)
