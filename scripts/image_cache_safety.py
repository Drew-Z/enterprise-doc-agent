"""Read-only capacity and original-cache checks for a complete OCI import batch."""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import IO, Any, NamedTuple

NAMESPACE = "enterprise-doc-agent-staging"
WORKLOADS = {"enterprise-doc-" + role for role in ("api", "worker", "consumer", "web", "redis")}
CACHE_DIRECTORY = "/var/lib/rancher/k3s/agent/containerd"
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_LAYER_BYTES = 8 * 1024**3
MAX_LAYER_INODES = 2_000_000
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
LAYER_TYPES = {
    "application/vnd.oci.image.layer.v1.tar": False,
    "application/vnd.oci.image.layer.v1.tar+gzip": True,
    "application/vnd.docker.image.rootfs.diff.tar.gzip": True,
}
Run = Callable[..., subprocess.CompletedProcess[str]]


class ImageCacheSafetyError(ValueError):
    """A missing fact or failed guard must stop the cache write."""


class ArchiveFootprint(NamedTuple):
    content_bytes: int
    snapshot_bytes: int
    snapshot_inodes: int
    unpack_temporary_bytes: int
    normalized_archive_bytes: int
    content_inodes: int = 0


class LayerReader(io.RawIOBase):
    def __init__(self, stream: gzip.GzipFile | IO[bytes], checkpoint: Callable[[], None]) -> None:
        super().__init__()
        self.stream = stream
        self.checkpoint = checkpoint
        self.sha256 = hashlib.sha256()
        self.bytes = 0

    def read(self, size: int = -1) -> bytes:
        self.checkpoint()
        if size < 0:
            size = 1024 * 1024
        data = self.stream.read(min(size, MAX_LAYER_BYTES - self.bytes + 1))
        self.bytes += len(data)
        if self.bytes > MAX_LAYER_BYTES:
            raise ImageCacheSafetyError("OCI layer exceeds the expansion bound")
        self.sha256.update(data)
        return data


def _blob(archive: tarfile.TarFile, digest: object) -> tarfile.TarInfo:
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ImageCacheSafetyError("invalid OCI content digest")
    name = "blobs/sha256/" + digest.removeprefix("sha256:")
    matches = [item for item in archive.getmembers() if item.name.removeprefix("./") == name]
    if len(matches) != 1 or not matches[0].isfile():
        raise ImageCacheSafetyError("OCI archive is missing unique required content")
    return matches[0]


def _json_blob(archive: tarfile.TarFile, digest: object) -> dict[str, Any]:
    item = _blob(archive, digest)
    if item.size > 16 * 1024**2:
        raise ImageCacheSafetyError("OCI metadata is not bounded")
    stream = archive.extractfile(item)
    if stream is None:
        raise ImageCacheSafetyError("OCI metadata is unreadable")
    with stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ImageCacheSafetyError("OCI metadata must be an object")
    return value


def archive_footprint(
    path: Path,
    image_digests: Sequence[str],
    *,
    checkpoint: Callable[[], None] = lambda: None,
) -> ArchiveFootprint:
    """Measure verified content and native snapshots without extracting any layer."""
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ImageCacheSafetyError("OCI archive exceeds the input bound")
    content_bytes = 0
    snapshot_bytes = 0
    inodes = 0
    largest_layer = 0
    layer_sizes: dict[str, tuple[int, int, str]] = {}
    archive_bytes = 0
    runtime_images = 0
    try:
        with tarfile.open(path, "r:*") as archive:
            seen = set()
            for member in archive.getmembers():
                checkpoint()
                name = member.name.removeprefix("./").rstrip("/")
                if name in seen:
                    raise ImageCacheSafetyError("duplicate OCI archive member")
                seen.add(name)
                if member.isdir() and name in {".", "blobs", "blobs/sha256"}:
                    continue
                if not member.isfile() or member.sparse is not None:
                    raise ImageCacheSafetyError("unsupported OCI archive member")
                if name not in {"index.json", "oci-layout"} and not re.fullmatch(
                    r"blobs/sha256/[0-9a-f]{64}", name
                ):
                    raise ImageCacheSafetyError("unexpected OCI archive member")
                if member.size > MAX_ARCHIVE_BYTES:
                    raise ImageCacheSafetyError("OCI content exceeds the input bound")
                content_bytes += math.ceil(member.size / 4096) * 4096
                archive_bytes += 512 + math.ceil(member.size / 512) * 512
                if archive_bytes > MAX_ARCHIVE_BYTES or len(seen) > 100000:
                    raise ImageCacheSafetyError("expanded OCI archive exceeds the input bound")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ImageCacheSafetyError("OCI content is unreadable")
                with stream:
                    content_hash = hashlib.sha256()
                    while chunk := stream.read(1024**2):
                        checkpoint()
                        content_hash.update(chunk)
                    sha256 = content_hash.hexdigest()
                if name.startswith("blobs/") and sha256 != name.rsplit("/", 1)[-1]:
                    raise ImageCacheSafetyError("OCI content digest mismatch")
            for image_digest in image_digests:
                manifest = _json_blob(archive, image_digest)
                if "layers" not in manifest:
                    continue  # Index metadata is counted above; leaf manifests are measured here.
                config = manifest.get("config")
                layers = manifest["layers"]
                if not isinstance(config, dict) or not isinstance(layers, list):
                    raise ImageCacheSafetyError("OCI manifest lacks its config or layers")
                image_config = _json_blob(archive, config.get("digest"))
                for descriptor in [config, *layers]:
                    if not isinstance(descriptor, dict):
                        raise ImageCacheSafetyError("invalid OCI content descriptor")
                    item = _blob(archive, descriptor.get("digest"))
                    if type(descriptor.get("size")) is not int or item.size != descriptor["size"]:
                        raise ImageCacheSafetyError("OCI content size mismatch")
                if image_config.get("os") == image_config.get("architecture") == "unknown":
                    continue  # Attestation payloads have no runtime snapshot.
                if image_config.get("os") != "linux" or image_config.get("architecture") != "amd64":
                    raise ImageCacheSafetyError(
                        "only reviewed linux/amd64 runtime images are supported"
                    )
                runtime_images += 1
                rootfs = image_config.get("rootfs", {})
                diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
                if (
                    not isinstance(diff_ids, list)
                    or rootfs.get("type") != "layers"
                    or len(diff_ids) != len(layers)
                ):
                    raise ImageCacheSafetyError("OCI rootfs layer identities are missing")
                for descriptor, diff_id in zip(layers, diff_ids, strict=True):
                    digest = descriptor["digest"]
                    compressed = LAYER_TYPES.get(descriptor.get("mediaType"))
                    if compressed is None:
                        raise ImageCacheSafetyError("OCI layer encoding is not supported")
                    if digest not in layer_sizes:
                        stream = archive.extractfile(_blob(archive, digest))
                        if stream is None:
                            raise ImageCacheSafetyError("OCI layer is unreadable")
                        with stream:
                            decoded = gzip.GzipFile(fileobj=stream) if compressed else stream
                            try:
                                reader = LayerReader(decoded, checkpoint)
                                paths: set[PurePosixPath] = set()
                                with tarfile.open(fileobj=reader, mode="r|") as layer:
                                    for member in layer:
                                        if member.sparse is not None:
                                            raise ImageCacheSafetyError(
                                                "sparse OCI layers are unsupported"
                                            )
                                        layer_path = PurePosixPath(member.name.removeprefix("./"))
                                        if layer_path.is_absolute() or ".." in layer_path.parts:
                                            raise ImageCacheSafetyError("invalid OCI layer path")
                                        paths.update(
                                            parent
                                            for parent in (layer_path, *layer_path.parents)
                                            if str(parent) != "."
                                        )
                                        if len(paths) > MAX_LAYER_INODES:
                                            raise ImageCacheSafetyError(
                                                "OCI layer exceeds the inode bound"
                                            )
                                while reader.read(1024 * 1024):
                                    pass
                                layer_sizes[digest] = (
                                    reader.bytes,
                                    len(paths),
                                    "sha256:" + reader.sha256.hexdigest(),
                                )
                            finally:
                                if compressed:
                                    decoded.close()
                    size, count, observed_diff = layer_sizes[digest]
                    if observed_diff != diff_id:
                        raise ImageCacheSafetyError("OCI expanded layer diff_id mismatch")
                    # Count shared layers in every image chain; do not assume snapshot reuse.
                    snapshot_bytes += size + count * 16384
                    inodes += count
                    largest_layer = max(largest_layer, size)
    except (OSError, EOFError, tarfile.TarError, json.JSONDecodeError, zlib.error) as error:
        raise ImageCacheSafetyError("unable to measure complete OCI content") from error
    if not runtime_images:
        raise ImageCacheSafetyError("OCI archive has no supported runtime image")
    return ArchiveFootprint(
        content_bytes,
        snapshot_bytes,
        inodes,
        largest_layer,
        archive_bytes + 16 * 1024**2,
        len(seen),
    )


def _quantity(value: object, total: int) -> int:
    if not isinstance(value, str):
        raise ImageCacheSafetyError("eviction policy is missing or invalid")
    if value.endswith("%"):
        try:
            percent = Decimal(value[:-1])
        except InvalidOperation:
            raise ImageCacheSafetyError("invalid eviction percentage") from None
        if not percent.is_finite() or not 0 <= percent <= 100:
            raise ImageCacheSafetyError("invalid eviction percentage")
        return math.ceil(Decimal(total) * percent / 100)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|K|M|G)?", value)
    if match is None:
        raise ImageCacheSafetyError("unsupported eviction quantity")
    units = {
        None: 1,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
    }
    return math.ceil(Decimal(match[1]) * units[match[2]])


def validate_capacity(
    state: dict[str, Any],
    footprints: Sequence[ArchiveFootprint],
    *,
    normalized: bool = False,
    previous: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not footprints or not 0 <= clock() - state.get("observed_at", -math.inf) <= 15:
        raise ImageCacheSafetyError("cache observation is missing or stale")
    if state.get("ready") is not True or state.get("disk_pressure") is not False:
        raise ImageCacheSafetyError("node readiness or DiskPressure prevents prewarm")
    binding: dict[str, Any] = {
        key: state.get(key)
        for key in (
            "namespace_uid",
            "node_uid",
            "boot_id",
            "workloads_sha256",
            "eviction_hard",
            "eviction_soft",
            "minimum_reclaim",
            "required_refs",
        )
    }
    if any(binding[key] is None for key in binding):
        raise ImageCacheSafetyError("cache target binding is incomplete")
    binding = copy.deepcopy(binding)
    if any(
        not isinstance(binding[key], dict)
        for key in ("eviction_hard", "eviction_soft", "minimum_reclaim")
    ):
        raise ImageCacheSafetyError("eviction policy is missing or invalid")
    if previous is not None and binding != previous["binding"]:
        raise ImageCacheSafetyError("original cache target or eviction policy changed")
    refs = state.get("required_refs", [])
    images = state.get("images", {})
    if not refs or set(refs) != set(images):
        raise ImageCacheSafetyError("original runtime image inventory is incomplete")
    for ref in refs:
        if canonical_reference(ref) != ref:
            raise ImageCacheSafetyError("original runtime image reference is not canonical")
        image = images[ref]
        if image.get("ready") is not True or image.get("target") != ref.rsplit("@", 1)[-1]:
            raise ImageCacheSafetyError("original image content or unpacked snapshot is missing")
        aliases = image.get("aliases", {})
        if ref not in aliases or any(digest != image["target"] for digest in aliases.values()):
            raise ImageCacheSafetyError("original CRI alias is missing or mismatched")
    cache = state["cache_filesystem"]
    temporary = state["temporary_filesystem"]
    node = state.get("node_filesystem")
    if not isinstance(node, dict):
        raise ImageCacheSafetyError("node filesystem observation is missing")
    devices = [cache["device"], temporary["device"], node["device"]]
    if previous is not None and devices != previous["devices"]:
        raise ImageCacheSafetyError("image or temporary filesystem changed")
    permanent = sum(item.content_bytes + item.snapshot_bytes for item in footprints)
    unpack_temp = max(item.unpack_temporary_bytes for item in footprints)
    cache_bytes = math.ceil((permanent + unpack_temp) * 5 / 4) + 64 * 1024**2
    cache_inodes = (
        math.ceil(sum(item.snapshot_inodes + item.content_inodes for item in footprints) * 5 / 4)
        + 4096
    )
    temp_bytes = 0 if normalized else sum(item.normalized_archive_bytes for item in footprints)
    allocations = [
        (cache, cache_bytes, cache_inodes, "imagefs"),
        (temporary, temp_bytes, len(footprints) + 1, "nodefs"),
        (node, 0, 0, "nodefs"),
    ]
    requirements: dict[int, tuple[dict[str, Any], int, int, set[str]]] = {}
    for fs, allocated_bytes, allocated_inodes, signal in allocations:
        previous_allocation = requirements.get(fs["device"])
        if previous_allocation is None:
            requirements[fs["device"]] = (dict(fs), allocated_bytes, allocated_inodes, {signal})
        else:
            shared, old_bytes, old_inodes, signals = previous_allocation
            if any(fs[key] != shared[key] for key in ("total_bytes", "total_inodes")):
                raise ImageCacheSafetyError("filesystem capacity changed during observation")
            shared["available_bytes"] = min(fs["available_bytes"], shared["available_bytes"])
            shared["available_inodes"] = min(fs["available_inodes"], shared["available_inodes"])
            requirements[fs["device"]] = (
                shared,
                old_bytes + allocated_bytes,
                old_inodes + allocated_inodes,
                signals | {signal},
            )
    checks = []
    for filesystem, bytes_needed, inodes_needed, signals in requirements.values():
        for key in ("device", "total_bytes", "available_bytes", "total_inodes", "available_inodes"):
            if type(filesystem.get(key)) is not int or filesystem[key] < 0:
                raise ImageCacheSafetyError("filesystem observation is invalid")
        if filesystem["total_bytes"] <= 0 or filesystem["total_inodes"] <= 0:
            raise ImageCacheSafetyError("filesystem size or inode capacity is missing")
        floors = []
        inode_floors = [math.ceil(filesystem["total_inodes"] * 0.05)]
        for prefix in signals:
            key = prefix + ".available"
            hard = binding["eviction_hard"].get(key)
            soft = binding["eviction_soft"].get(key, "0")
            reclaim = binding["minimum_reclaim"].get(key, "0")
            floors.append(
                max(
                    _quantity(hard, filesystem["total_bytes"]),
                    _quantity(soft, filesystem["total_bytes"]),
                )
                + _quantity(reclaim, filesystem["total_bytes"])
            )
            inode_key = prefix + ".inodesFree"
            if inode_key in binding["eviction_hard"] or inode_key in binding["eviction_soft"]:
                inode_floors.append(
                    max(
                        _quantity(
                            binding["eviction_hard"].get(inode_key, "0"), filesystem["total_inodes"]
                        ),
                        _quantity(
                            binding["eviction_soft"].get(inode_key, "0"), filesystem["total_inodes"]
                        ),
                    )
                    + _quantity(
                        binding["minimum_reclaim"].get(inode_key, "0"), filesystem["total_inodes"]
                    )
                )
        reserve, inode_reserve = max(floors), max(inode_floors)
        if filesystem["available_bytes"] - bytes_needed < reserve:
            raise ImageCacheSafetyError(
                "insufficient aggregate image-cache space including eviction/reclaim reserve"
            )
        if filesystem["available_inodes"] - inodes_needed < inode_reserve:
            raise ImageCacheSafetyError("insufficient image-cache inode reserve")
        checks.append(
            {
                "device": filesystem["device"],
                "required_bytes": bytes_needed,
                "reserve_bytes": reserve,
                "available_bytes": filesystem["available_bytes"],
                "required_inodes": inodes_needed,
                "reserve_inodes": inode_reserve,
            }
        )
    return {
        "binding": binding,
        "devices": devices,
        "filesystems": checks,
        "rollback_refs_verified": refs,
        "observed_at": state["observed_at"],
    }


def canonical_reference(ref: str) -> str:
    if not isinstance(ref, str) or "@" not in ref or not DIGEST.fullmatch(ref.rsplit("@", 1)[1]):
        raise ImageCacheSafetyError("original runtime images must use immutable digests")
    if "/" not in ref:
        return "docker.io/library/" + ref
    first = ref.split("/", 1)[0]
    return ref if "." in first or ":" in first or first == "localhost" else "docker.io/" + ref


def temporary_directory() -> str:
    """Select a directory without tempfile.gettempdir's write-based discovery probe."""
    candidates: list[str | None]
    if tempfile.tempdir is not None:
        if not isinstance(tempfile.tempdir, str):
            raise ImageCacheSafetyError("temporary directory must be a text path")
        candidates = [tempfile.tempdir]
    else:
        candidates = [os.environ.get(key) for key in ("TMPDIR", "TEMP", "TMP")]
        candidates.extend(["/tmp", "/var/tmp", "/usr/tmp"])
    for candidate in candidates:
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK | os.X_OK):
            return os.path.abspath(candidate)
    raise ImageCacheSafetyError("no writable temporary directory is available")


def collect_cache_state(prefix: tuple[str, ...], *, run: Run = subprocess.run) -> dict[str, Any]:
    """Do not read Secrets, pull images, tag aliases or change kubelet configuration."""
    if sys.platform != "linux" or Path(prefix[0]).name != "k3s":
        raise ImageCacheSafetyError("live prewarm requires the reviewed Linux k3s target")
    deadline = time.monotonic() + 90
    observed_at = time.monotonic()

    def command(args: list[str]) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImageCacheSafetyError("cache observation deadline expired")
        try:
            return run(
                [prefix[0], *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=min(20, remaining),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise ImageCacheSafetyError("read-only cache observation failed") from None

    def payload(args: list[str]) -> dict[str, Any]:
        try:
            value = json.loads(command(args))
        except json.JSONDecodeError:
            raise ImageCacheSafetyError("invalid cache observation response") from None
        if not isinstance(value, dict):
            raise ImageCacheSafetyError("invalid cache observation response")
        return value

    namespace = payload(["kubectl", "get", "namespace", NAMESPACE, "-o", "json"])
    nodes = payload(["kubectl", "get", "nodes", "-o", "json"])["items"]
    if len(nodes) != 1 or nodes[0]["status"]["nodeInfo"]["architecture"] != "amd64":
        raise ImageCacheSafetyError("prewarm requires the reviewed single amd64 node")
    node = nodes[0]
    cfg = payload(
        ["kubectl", "get", "--raw", "/api/v1/nodes/" + node["metadata"]["name"] + "/proxy/configz"]
    )["kubeletconfig"]
    deployments = payload(["kubectl", "-n", NAMESPACE, "get", "deployments", "-o", "json"])["items"]
    if {item["metadata"]["name"] for item in deployments} != WORKLOADS:
        raise ImageCacheSafetyError("original five-workload inventory changed")
    refs: set[str] = set()
    specs = []
    for item in deployments:
        if item["spec"].get("replicas") != 1 or item.get("status", {}).get("readyReplicas") != 1:
            raise ImageCacheSafetyError("original workload is not ready")
        specs.append(
            {"uid": item["metadata"]["uid"], "name": item["metadata"]["name"], "spec": item["spec"]}
        )
        pod = item["spec"]["template"]["spec"]
        refs.update(
            canonical_reference(container["image"])
            for container in [*pod.get("containers", []), *pod.get("initContainers", [])]
        )
    rows = command(["ctr", "--namespace", "k8s.io", "images", "list"]).splitlines()
    targets = {row[0]: row[2] for line in rows[1:] if len(row := line.split()) >= 3}
    images = {}
    for ref in sorted(refs):
        ready = command(
            [
                "ctr",
                "--namespace",
                "k8s.io",
                "images",
                "check",
                "--snapshotter",
                "overlayfs",
                "--quiet",
                "name==" + ref,
            ]
        ).splitlines()
        status = payload(["crictl", "inspecti", "-o", "json", ref])["status"]
        aliases = status.get("repoDigests", [])
        images[ref] = {
            "ready": ref in ready,
            "target": targets.get(ref),
            "aliases": {alias: targets.get(alias) for alias in aliases},
        }

    def filesystem(path: str) -> dict[str, int]:
        info = os.statvfs(path)
        if info.f_frsize > 4096:
            raise ImageCacheSafetyError("filesystem allocation size exceeds reviewed OCI estimate")
        return {
            "device": os.stat(path).st_dev,
            "total_bytes": info.f_blocks * info.f_frsize,
            "available_bytes": info.f_bavail * info.f_frsize,
            "total_inodes": info.f_files,
            "available_inodes": info.f_favail,
        }

    conditions = {item["type"]: item["status"] for item in node["status"]["conditions"]}
    return {
        "observed_at": observed_at,
        "namespace_uid": namespace["metadata"]["uid"],
        "node_uid": node["metadata"]["uid"],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "workloads_sha256": hashlib.sha256(
            json.dumps(
                sorted(specs, key=lambda item: item["name"]), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "ready": conditions.get("Ready") == "True",
        "disk_pressure": conditions.get("DiskPressure") != "False",
        "eviction_hard": cfg.get("evictionHard"),
        "eviction_soft": cfg.get("evictionSoft") or {},
        "minimum_reclaim": cfg.get("evictionMinimumReclaim") or {},
        "required_refs": sorted(refs),
        "images": images,
        "cache_filesystem": filesystem(CACHE_DIRECTORY),
        "temporary_filesystem": filesystem(temporary_directory()),
        "node_filesystem": filesystem("/"),
    }
