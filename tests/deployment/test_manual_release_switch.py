import pytest
from scripts.maintenance_guard import GuardError
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_release_switch import Boundary
from tests.deployment.test_workbook_release_switch import workbook_switch_data


def manual_switch_data(original=False, candidate=True):
    data = workbook_switch_data(original=True)
    data.update(
        original_revision="20261009_0036",
        manual_readers={"original": original, "candidate": candidate},
    )
    return data


def test_0036_image_switch_and_empty_manual_history_rollback_preserve_workloads():
    data = manual_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: False,
        manual_empty=lambda timeout: True,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize("field", ["workbook_readers", "manual_readers"])
@pytest.mark.parametrize(
    "readers",
    [
        None,
        {},
        {"original": False},
        {"original": 0, "candidate": True},
        {"original": False, "candidate": "true"},
        {"original": False, "candidate": True, "unchecked": True},
    ],
)
def test_0036_requires_both_exact_boolean_reader_capabilities(field, readers):
    data = manual_switch_data()
    if readers is None:
        del data[field]
    else:
        data[field] = readers
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("history", ["workbook", "manual"])
@pytest.mark.parametrize("action", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_0036_history_blocks_legacy_readers_at_both_race_boundaries(history, action, during_close):
    data = manual_switch_data(original=True)
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
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: empty[0] if history == "workbook" else False,
        manual_empty=lambda timeout: empty[0] if history == "manual" else False,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        getattr(cluster, action)(100)
    if during_close:
        assert all(x["spec"]["replicas"] == 0 for x in boundary.items if x["kind"] == "Deployment")
        assert all(kind == "deployment" for kind, _ in boundary.writes)
        for original in data["deployments"]:
            current = next(
                x for x in boundary.items if x["metadata"]["name"] == original["metadata"]["name"]
            )
            assert current["spec"] == original["spec"] | {"replicas": 0}
    else:
        assert not boundary.writes


def test_manual_candidate_incompatibility_blocks_apply_but_allows_compatible_original_restore():
    data = manual_switch_data(original=True, candidate=False)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: False,
        manual_empty=lambda timeout: False,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes
    cluster.restore(100)
    cluster.verify(False, 100)


def test_compatible_readers_can_apply_and_restore_with_both_histories():
    data = manual_switch_data(original=True)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: False,
        manual_empty=lambda timeout: False,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize("during_close", [False, True])
def test_manual_rollback_keeps_newly_admitted_work_away_from_old_workers(during_close):
    data = manual_switch_data(original=True)
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
        revision=lambda timeout: "20261009_0036",
        idle=lambda timeout: idle[0],
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.restore(100)
    assert not boundary.writes or (
        during_close and all(kind == "deployment" for kind, _ in boundary.writes)
    )


@pytest.mark.parametrize("mutation", ["mode", "config", "credential", "pool", "revision"])
def test_0036_refuses_unrelated_release_changes(mutation):
    data = manual_switch_data()
    if mutation == "mode":
        data["release_kind"] = "reasoning_only"
    elif mutation == "config":
        data["candidate_prerequisites"][1]["data"]["MODEL__TIMEOUT_SECONDS"] = "299"
    elif mutation == "credential":
        data["secret"]["new_key"] = "bmV3"
    elif mutation == "pool":
        data["api_database_pool_size"] = 2
    else:
        data["original_revision"] = "20261009_0035"
    with pytest.raises(GuardError):
        ReleasePlan(data)


def test_0036_live_revision_drift_causes_no_write():
    data = manual_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0035",
        idle=lambda timeout: True,
        manual_empty=lambda timeout: True,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes
