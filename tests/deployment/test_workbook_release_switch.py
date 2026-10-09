import pytest
from scripts.maintenance_guard import GuardError
from scripts.release_switch import ReleaseCluster, ReleasePlan
from tests.deployment.test_release_switch import Boundary, image_switch_data


def workbook_switch_data(original=False, candidate=True):
    data = image_switch_data()
    data.update(
        original_revision="20261009_0035",
        workbook_readers={"original": original, "candidate": candidate},
    )
    return data


def test_0035_image_switch_and_empty_history_rollback_preserve_workloads():
    data = workbook_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0035",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: True,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.verify(True, 100)
    cluster.restore(100)
    cluster.verify(False, 100)


@pytest.mark.parametrize(
    "readers",
    [
        None,
        {},
        {"original": False},
        {"original": "false", "candidate": True},
        {"original": False, "candidate": True, "unchecked": True},
    ],
)
def test_0035_requires_exact_boolean_reader_capabilities(readers):
    data = workbook_switch_data()
    if readers is None:
        del data["workbook_readers"]
    else:
        data["workbook_readers"] = readers
    with pytest.raises(GuardError):
        ReleasePlan(data)


@pytest.mark.parametrize("action", ["apply", "restore"])
@pytest.mark.parametrize("during_close", [False, True])
def test_workbook_history_blocks_legacy_reader_before_and_after_close(action, during_close):
    data = workbook_switch_data()
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
        revision=lambda timeout: "20261009_0035",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: empty[0],
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        getattr(cluster, action)(100)
    if not during_close:
        assert not boundary.writes
    else:
        assert all(x["spec"]["replicas"] == 0 for x in boundary.items if x["kind"] == "Deployment")
        assert all(kind == "deployment" for kind, _ in boundary.writes)


def test_compatible_readers_can_restore_with_workbook_history():
    data = workbook_switch_data(original=True)
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261009_0035",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: False,
        clock=lambda: 1,
    )
    cluster.apply(100)
    cluster.restore(100)
    cluster.verify(False, 100)


def test_0035_rejects_other_release_modes_and_drift():
    data = workbook_switch_data()
    data["release_kind"] = "reasoning_only"
    with pytest.raises(GuardError):
        ReleasePlan(data)
    data = workbook_switch_data()
    boundary = Boundary(data)
    cluster = ReleaseCluster(
        ReleasePlan(data),
        run=boundary,
        revision=lambda timeout: "20261008_0034",
        idle=lambda timeout: True,
        workbook_empty=lambda timeout: True,
        clock=lambda: 1,
    )
    with pytest.raises(GuardError):
        cluster.apply(100)
    assert not boundary.writes
