import pytest
from scripts.maintenance_guard import GuardError
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_manual_release_switch import manual_switch_data
from tests.deployment.test_release_switch import Boundary


def citation_switch_data(original=False, candidate=True):
    data = manual_switch_data(original=True)
    data.update(
        original_revision="20261010_0037",
        citation_readers={"original": original, "candidate": candidate},
        missing_information_readers={"original": True, "candidate": True},
    )
    return data


def test_0037_images_and_empty_citation_history_allow_rollback():
    data = citation_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261010_0037",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: False,
        manual_empty=lambda timeout: False,
        citation_empty=lambda timeout: True,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize("field", ["workbook_readers", "manual_readers", "citation_readers"])
@pytest.mark.parametrize(
    "readers",
    [
        None,
        {},
        {"original": False},
        {"original": 0, "candidate": True},
        {"original": False, "candidate": "true"},
        {"original": False, "candidate": True, "extra": True},
    ],
)
def test_0037_requires_exact_explicit_reader_capabilities(field, readers):
    data = citation_switch_data()
    if readers is None:
        del data[field]
    else:
        data[field] = readers
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("history", ["workbook", "manual", "citation"])
@pytest.mark.parametrize("action", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_0037_history_blocks_incompatible_readers_at_both_boundaries(history, action, during_close):
    data = citation_switch_data(original=True)
    data[history + "_readers"]["original"] = False
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
        revision=lambda timeout: "20261010_0037",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: empty[0] if history == "workbook" else False,
        manual_empty=lambda timeout: empty[0] if history == "manual" else False,
        citation_empty=lambda timeout: empty[0] if history == "citation" else False,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        getattr(cluster, action)(100)
    if during_close:
        assert all(kind == "deployment" for kind, _ in boundary.writes)
        for original in data["deployments"]:
            current = next(
                x for x in boundary.items if x["metadata"]["name"] == original["metadata"]["name"]
            )
            assert current["spec"] == original["spec"] | {"replicas": 0}
    else:
        assert not boundary.writes


def test_candidate_incompatibility_refuses_apply_but_original_can_restore_with_citations():
    data = citation_switch_data(original=True, candidate=False)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261010_0037",
        idle=lambda timeout: True,
        citation_empty=lambda timeout: False,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize("mutation", ["mode", "config", "credential", "pool", "revision"])
def test_0037_refuses_unrelated_changes(mutation):
    data = citation_switch_data()
    if mutation == "mode":
        data["release_kind"] = "reasoning_only"
    elif mutation == "config":
        data["candidate_prerequisites"][1]["data"]["MODEL__TIMEOUT_SECONDS"] = "299"
    elif mutation == "credential":
        data["secret"]["new_key"] = "bmV3"
    elif mutation == "pool":
        data["api_database_pool_size"] = 2
    else:
        data["original_revision"] = "20261009_0036"
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("during_close", [False, True])
def test_citation_rollback_keeps_racing_active_work_stopped(during_close):
    data = citation_switch_data(original=True)
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
        revision=lambda timeout: "20261010_0037",
        idle=lambda timeout: idle[0],
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.restore(100)
    assert not boundary.writes or (
        during_close and all(kind == "deployment" for kind, _ in boundary.writes)
    )


def test_citation_live_revision_drift_never_writes():
    data = citation_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: True,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes
