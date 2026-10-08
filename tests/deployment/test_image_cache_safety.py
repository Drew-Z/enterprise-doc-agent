from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import math
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from scripts import image_cache_safety as safety
from scripts import import_staging_oci_archive as receiver


def archive_fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cache_archive_fixture", Path(__file__).with_name("test_import_staging_oci_archive.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plans_for(tmp_path: Path, count: int = 1) -> list[receiver.OciImportPlan]:
    fixture = archive_fixture()
    plans = []
    for i in range(count):
        path = tmp_path / f"{i}.oci.tar"
        sha, _ = fixture._write_oci_archive(path)
        plans.append(
            receiver.prepare_import_plan(
                archive=path, expected_sha256=sha, base_name=f"batch-{i}", containerd_cli="k3s"
            )
        )
    return plans


class Containerd:
    def __init__(self, plans: list[receiver.OciImportPlan]):
        self.plans = plans
        self.commands: list[list[str]] = []
        self.archives: list[Path] = []

    def run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert 0 < kwargs["timeout"] <= 120
        self.commands.append(command)
        if "import" in command:
            path = Path(command[-1])
            assert path.is_file()
            self.archives.append(path)
        output = (
            "\n".join(ref for plan in self.plans for ref in plan.expected_refs)
            if command[-1] == "--quiet"
            else ""
        )
        return subprocess.CompletedProcess(command, 0, output, "")


def layer_archive(
    tmp_path: Path, *, outer_gzip: bool = False, bad_diff: bool = False
) -> receiver.OciImportPlan:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as layer:
        info = tarfile.TarInfo("data/payload")
        content = b"x" * (20 * 1024**2)
        info.size = len(content)
        layer.addfile(info, io.BytesIO(content))
    # The uncompressed layer inside a compressed OCI envelope exercises normalization.
    layer_data = raw.getvalue() if outer_gzip else gzip.compress(raw.getvalue())
    blobs = {}

    def blob(content: bytes, kind: str) -> dict[str, object]:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        blobs["blobs/sha256/" + digest[7:]] = content
        return {"digest": digest, "size": len(content), "mediaType": kind}

    config = json.dumps(
        {
            "os": "linux",
            "architecture": "amd64",
            "rootfs": {
                "type": "layers",
                "diff_ids": [
                    "sha256:"
                    + ("0" * 64 if bad_diff else hashlib.sha256(raw.getvalue()).hexdigest())
                ],
            },
        }
    ).encode()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": blob(config, "application/vnd.oci.image.config.v1+json"),
            "layers": [
                blob(
                    layer_data,
                    "application/vnd.oci.image.layer.v1.tar" + ("" if outer_gzip else "+gzip"),
                )
            ],
        }
    ).encode()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [blob(manifest, "application/vnd.oci.image.manifest.v1+json")],
        }
    ).encode()
    path = tmp_path / "layers.oci.tar"
    with tarfile.open(path, "w:gz" if outer_gzip else "w") as archive:
        for name, content in {
            "index.json": index,
            "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
            **blobs,
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return receiver.prepare_import_plan(
        archive=path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        base_name="layers",
        containerd_cli="k3s",
    )


def test_normalization_budget_counts_expanded_outer_archive(tmp_path: Path) -> None:
    plan = layer_archive(tmp_path, outer_gzip=True)
    footprint = safety.archive_footprint(plan.archive, plan.descriptor_digests)
    assert plan.archive.stat().st_size < 100000
    assert footprint.snapshot_bytes >= 20 * 1024**2
    assert footprint.normalized_archive_bytes >= 20 * 1024**2


def test_batch_has_no_import_when_only_one_archive_fits(tmp_path: Path) -> None:
    fixture_path = Path(__file__).with_name("test_import_staging_oci_archive.py")
    spec = importlib.util.spec_from_file_location("cache_archive_fixture", fixture_path)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    plans = []
    for number in range(2):
        archive = tmp_path / f"image-{number}.tar"
        checksum, _ = fixture._write_oci_archive(archive)
        plans.append(
            receiver.prepare_import_plan(
                archive=archive,
                expected_sha256=checksum,
                base_name=f"bounded-{number}",
                containerd_cli="k3s",
            )
        )
    commands = []
    footprint = safety.archive_footprint(plans[0].archive, plans[0].descriptor_digests)
    state = fixture.healthy_cache_state()
    fs = state["cache_filesystem"]
    single = safety.validate_capacity(state, [footprint])["filesystems"][0]
    fs["available_bytes"] = (
        single["reserve_bytes"]
        + single["required_bytes"]
        + math.ceil(footprint.normalized_archive_bytes / 2)
    )
    state["temporary_filesystem"] = dict(fs)
    safety.validate_capacity(state, [footprint])

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(receiver.StagingOciImportError, match="space"):
        receiver.execute_import_batch(plans, run=run, probe=lambda _: state)
    assert commands == []


def test_real_compressed_layer_and_batch_import_cleanup(tmp_path: Path) -> None:
    plans = [layer_archive(tmp_path), *plans_for(tmp_path)]
    state = archive_fixture().healthy_cache_state
    runner = Containerd(plans)
    report = receiver.execute_import_batch(plans, run=runner.run, probe=lambda _: state())
    assert report["status"] == "passed"
    assert len(report["imports"]) == 2
    assert report["cache_safety"]["preflight"]["filesystems"][0]["required_bytes"] > 40 * 1024**2
    assert len(runner.archives) == 2
    assert all(not path.exists() for path in runner.archives)


def test_content_store_inodes_are_reserved_before_any_temporary_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plans = plans_for(tmp_path)
    state = archive_fixture().healthy_cache_state()
    # Empty runtime layers still import config, manifest and attestation blobs.
    # This leaves room for the fixed metadata allowance and temporary directory,
    # but no content files above the 5% inode reserve.
    for key in ("cache_filesystem", "temporary_filesystem", "node_filesystem"):
        state[key]["available_inodes"] = 50000 + 4096 + 2

    def no_temp(*args: object, **kwargs: object) -> None:
        pytest.fail("content inode shortage created temporary files")

    monkeypatch.setattr(receiver.tempfile, "TemporaryDirectory", no_temp)
    runner = Containerd(plans)
    with pytest.raises(receiver.StagingOciImportError, match="inode"):
        receiver.execute_import_batch(plans, run=runner.run, probe=lambda _: state)
    assert runner.commands == []


@pytest.mark.parametrize(
    "fault",
    [
        "missing_redis",
        "wrong_alias",
        "not_unpacked",
        "stale",
        "disk_pressure",
        "inode",
        "space",
        "policy",
    ],
)
def test_preflight_failures_never_create_temporary_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    plans = plans_for(tmp_path)
    state = archive_fixture().healthy_cache_state()
    redis = next(ref for ref in state["required_refs"] if "redis@" in ref)
    if fault == "missing_redis":
        del state["images"][redis]
    elif fault == "wrong_alias":
        state["images"][redis]["aliases"][redis] = "sha256:" + "9" * 64
    elif fault == "not_unpacked":
        state["images"][redis]["ready"] = False
    elif fault == "stale":
        state["observed_at"] -= 16
    elif fault == "disk_pressure":
        state["disk_pressure"] = True
    elif fault in ("inode", "space"):
        state["cache_filesystem"]["available_inodes" if fault == "inode" else "available_bytes"] = 0
    else:
        state["eviction_hard"] = None

    def no_temp(*args: object, **kwargs: object) -> None:
        pytest.fail("unsafe batch created temporary files")

    monkeypatch.setattr(receiver.tempfile, "TemporaryDirectory", no_temp)
    runner = Containerd(plans)
    with pytest.raises(receiver.StagingOciImportError):
        receiver.execute_import_batch(plans, run=runner.run, probe=lambda _: state)
    assert runner.commands == []


@pytest.mark.parametrize("fault", ["space", "workloads_sha256", "boot_id", "policy"])
def test_drift_after_normalization_stops_before_first_import(tmp_path: Path, fault: str) -> None:
    plans = plans_for(tmp_path)
    state = archive_fixture().healthy_cache_state()
    calls = 0

    def probe(_: tuple[str, ...]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == "space":
                state["cache_filesystem"]["available_bytes"] = 0
            elif fault == "policy":
                state["minimum_reclaim"]["imagefs.available"] = "11%"
            else:
                state[fault] = "changed"
        return state

    runner = Containerd(plans)
    with pytest.raises(receiver.StagingOciImportError):
        receiver.execute_import_batch(plans, run=runner.run, probe=probe)
    assert calls == 2
    assert runner.commands == []


def test_source_replaced_after_plan_is_rejected_before_write(tmp_path: Path) -> None:
    plans = plans_for(tmp_path)
    plans[0].archive.write_bytes(plans[0].archive.read_bytes() + b"changed")
    runner = Containerd(plans)
    with pytest.raises(receiver.StagingOciImportError, match="SHA-256"):
        receiver.execute_import_batch(plans, run=runner.run)
    assert runner.commands == []


def test_invalid_expanded_layer_identity_is_rejected(tmp_path: Path) -> None:
    plan = layer_archive(tmp_path, bad_diff=True)
    runner = Containerd([plan])
    with pytest.raises(receiver.StagingOciImportError, match="diff_id"):
        receiver.execute_import_batch([plan], run=runner.run)
    assert runner.commands == []


def test_batch_cli_is_offline_by_default(tmp_path: Path) -> None:
    plans = plans_for(tmp_path, 2)
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archives": [
                    {
                        "archive": str(p.archive),
                        "expected_sha256": p.archive_sha256,
                        "base_name": p.canonical_base.rsplit("/", 1)[1],
                    }
                    for p in plans
                ],
            }
        )
    )
    result = subprocess.run(
        [sys.executable, str(Path(receiver.__file__)), "--batch-plan", str(batch)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "planned"
    assert report["mutation_performed"] is False
    assert len(report["archives"]) == 2


def test_timeout_is_unknown_and_is_not_retried(tmp_path: Path) -> None:
    plans = plans_for(tmp_path, 2)
    attempts = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        attempts.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    with pytest.raises(receiver.StagingOciImportError, match="result is unknown"):
        receiver.execute_import_batch(
            plans, run=run, probe=lambda _: archive_fixture().healthy_cache_state()
        )
    assert len(attempts) == 1
    assert not Path(attempts[0][-1]).exists()


def test_shared_deadline_does_not_restart_for_the_next_image(tmp_path: Path) -> None:
    plans = plans_for(tmp_path, 2)
    runner = Containerd(plans)
    elapsed = [100.0]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = runner.run(command, **kwargs)
        elapsed[0] += 1
        return result

    def probe(_: tuple[str, ...]) -> dict[str, object]:
        state = archive_fixture().healthy_cache_state()
        state["observed_at"] = elapsed[0]
        return state

    with pytest.raises(receiver.StagingOciImportError, match="shared OCI batch deadline"):
        receiver.execute_import_batch(
            plans, run=run, probe=probe, timeout=5, clock=lambda: elapsed[0]
        )
    assert len(runner.archives) == 1
    assert not runner.archives[0].exists()


def test_loss_of_original_cache_after_import_is_not_a_success(tmp_path: Path) -> None:
    plans = plans_for(tmp_path)
    runner = Containerd(plans)
    calls = 0

    def probe(_: tuple[str, ...]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        state = archive_fixture().healthy_cache_state()
        if calls == 3:
            ref = next(ref for ref in state["required_refs"] if "redis@" in ref)
            del state["images"][ref]
        return state

    with pytest.raises(receiver.StagingOciImportError, match="inventory"):
        receiver.execute_import_batch(plans, run=runner.run, probe=probe)
    assert len(runner.archives) == 1


def test_separate_cache_disk_cannot_hide_exhausted_node_filesystem(tmp_path: Path) -> None:
    plans = plans_for(tmp_path)
    state = archive_fixture().healthy_cache_state()
    state["node_filesystem"] = {**state["cache_filesystem"], "device": 2, "available_bytes": 0}
    runner = Containerd(plans)
    with pytest.raises(receiver.StagingOciImportError, match="space"):
        receiver.execute_import_batch(plans, run=runner.run, probe=lambda _: state)
    assert runner.commands == []


@pytest.mark.parametrize(
    "fault", [None, "init_missing", "alias_missing", "content_missing", "late_observation"]
)
def test_live_collector_checks_redis_and_init_containers_through_process_boundary(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    monkeypatch.setattr(safety.sys, "platform", "linux")
    elapsed = [100.0]
    monkeypatch.setattr(safety.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(safety, "temporary_directory", lambda: "/tmp")
    original_read = safety.Path.read_text
    monkeypatch.setattr(
        safety.Path,
        "read_text",
        lambda p, *a, **kw: (
            "boot"
            if str(p).replace("\\", "/") == "/proc/sys/kernel/random/boot_id"
            else original_read(p, *a, **kw)
        ),
    )
    original_stat = safety.os.stat
    monkeypatch.setattr(
        safety.os,
        "stat",
        lambda p, *a, **kw: (
            SimpleNamespace(st_dev=1)
            if str(p) in (safety.CACHE_DIRECTORY, "/tmp", "/")
            else original_stat(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        safety.os,
        "statvfs",
        lambda _: SimpleNamespace(
            f_frsize=4096, f_blocks=2500000, f_bavail=2000000, f_files=1000000, f_favail=900000
        ),
        raising=False,
    )
    init = "docker.io/library/init@sha256:" + "a" * 64
    refs = {
        "enterprise-doc-" + role: "docker.io/library/" + role + "@sha256:" + str(i) * 64
        for i, role in enumerate(("api", "worker", "consumer", "web", "redis"), 1)
    }
    namespace = {"metadata": {"uid": "namespace"}}
    nodes = {
        "items": [
            {
                "metadata": {"uid": "node", "name": "single"},
                "status": {
                    "nodeInfo": {"architecture": "amd64"},
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "DiskPressure", "status": "False"},
                    ],
                },
            }
        ]
    }
    deployments = {
        "items": [
            {
                "metadata": {"uid": role, "name": role},
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "image": ref.removeprefix("docker.io/library/")
                                    if "redis@" in ref
                                    else ref
                                }
                            ],
                            "initContainers": [{"image": init}]
                            if role == "enterprise-doc-api"
                            else [],
                        }
                    },
                },
                "status": {"readyReplicas": 1},
            }
            for role, ref in refs.items()
        ]
    }
    all_refs = [*refs.values(), init]
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert 0 < kwargs["timeout"] <= 20
        commands.append(command)
        if command[1] == "kubectl":
            if "namespace" in command:
                value = namespace
            elif "nodes" in command:
                value = nodes
            elif "--raw" in command:
                value = {
                    "kubeletconfig": {
                        "evictionHard": {"nodefs.available": "10%", "imagefs.available": "10%"},
                        "evictionMinimumReclaim": {
                            "nodefs.available": "10%",
                            "imagefs.available": "10%",
                        },
                    }
                }
            else:
                value = deployments
            output = json.dumps(value)
        elif command[1] == "crictl":
            aliases = [command[-1]]
            if fault == "alias_missing" and "redis@" in command[-1]:
                aliases.append("docker.io/library/old@" + command[-1].split("@")[1])
            output = json.dumps({"status": {"repoDigests": aliases}})
        elif command[-1] == "list":
            output = "REF TYPE DIGEST SIZE\n" + "\n".join(
                f"{ref} index {ref.split('@')[1]} 1B"
                for ref in all_refs
                if fault != "init_missing" or ref != init
            )
        else:
            ref = command[-1].removeprefix("name==")
            output = "" if fault == "content_missing" and "redis@" in ref else ref + "\n"
        if fault == "late_observation":
            elapsed[0] += 1
        return subprocess.CompletedProcess(command, 0, output, "")

    state = safety.collect_cache_state(("k3s", "ctr"), run=run)
    footprint = safety.ArchiveFootprint(100, 100, 1, 100, 100)
    if fault:
        with pytest.raises(safety.ImageCacheSafetyError):
            safety.validate_capacity(state, [footprint], clock=lambda: elapsed[0])
    else:
        result = safety.validate_capacity(state, [footprint], clock=lambda: elapsed[0])
        assert init in result["rollback_refs_verified"]
        assert any("redis@" in ref for ref in result["rollback_refs_verified"])
    assert all(
        "pull" not in command and "tag" not in command and "import" not in command
        for command in commands
    )
