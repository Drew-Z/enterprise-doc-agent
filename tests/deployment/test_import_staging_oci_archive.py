from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "import_staging_oci_archive.py"
SPEC = importlib.util.spec_from_file_location("import_staging_oci_archive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _descriptor(media_type: str, payload: bytes) -> dict[str, object]:
    return {
        "mediaType": media_type,
        "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        "size": len(payload),
    }


def _write_oci_archive(path: Path, *, member_prefix: str = "") -> tuple[str, tuple[str, ...]]:
    runtime_manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "1" * 64,
                "size": 2,
            },
            "layers": [],
        }
    )
    attestation_manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.unknown.config.v1+json",
                "digest": "sha256:" + "2" * 64,
                "size": 2,
            },
            "layers": [],
        }
    )
    runtime_descriptor = _descriptor("application/vnd.oci.image.manifest.v1+json", runtime_manifest)
    attestation_descriptor = _descriptor(
        "application/vnd.oci.image.manifest.v1+json", attestation_manifest
    )
    image_index = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [runtime_descriptor, attestation_descriptor],
        }
    )
    index_descriptor = _descriptor("application/vnd.oci.image.index.v1+json", image_index)
    archive_index = _json_bytes(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    **index_descriptor,
                    "annotations": {"org.opencontainers.image.ref.name": "enterprise-doc-api"},
                }
            ],
        }
    )
    blobs = {
        str(runtime_descriptor["digest"]): runtime_manifest,
        str(attestation_descriptor["digest"]): attestation_manifest,
        str(index_descriptor["digest"]): image_index,
    }
    with tarfile.open(path, "w") as archive:
        for name, content in {
            "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
            "index.json": archive_index,
            **{
                f"blobs/sha256/{digest.removeprefix('sha256:')}": content
                for digest, content in blobs.items()
            },
        }.items():
            info = tarfile.TarInfo(f"{member_prefix}{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    expected = tuple(sorted(blobs))
    return hashlib.sha256(path.read_bytes()).hexdigest(), expected


def test_import_plan_accepts_root_dot_slash_tar_members(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, expected_digests = _write_oci_archive(archive, member_prefix="./")

    plan = MODULE.prepare_import_plan(
        archive=archive,
        expected_sha256=archive_sha256,
        base_name="import-2026-08-17",
        containerd_cli="k3s",
    )

    assert plan.descriptor_digests == expected_digests


def test_import_plan_normalizes_short_base_and_covers_every_image_descriptor(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, expected_digests = _write_oci_archive(archive)

    plan = MODULE.prepare_import_plan(
        archive=archive,
        expected_sha256=archive_sha256,
        base_name="import-2026-08-17",
        containerd_cli="k3s",
    )

    canonical_base = "docker.io/library/import-2026-08-17"
    assert plan.canonical_base == canonical_base
    assert plan.descriptor_digests == expected_digests
    assert plan.import_command == (
        "k3s",
        "ctr",
        "--namespace",
        "k8s.io",
        "images",
        "import",
        "--all-platforms",
        "--digests",
        "--base-name",
        canonical_base,
        "<normalized-archive>",
    )
    assert plan.expected_refs == tuple(f"{canonical_base}@{digest}" for digest in expected_digests)


def test_execute_import_verifies_every_canonical_digest_alias(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, _ = _write_oci_archive(archive)
    plan = MODULE.prepare_import_plan(
        archive=archive,
        expected_sha256=archive_sha256,
        base_name="import-2026-08-17",
        containerd_cli="ctr",
    )
    commands: list[tuple[str, ...]] = []
    normalized_digests: tuple[str, ...] = ()

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal normalized_digests
        commands.append(tuple(command))
        if command[-2] == plan.canonical_base and command[-1] != "<normalized-archive>":
            with tarfile.open(command[-1], "r:*") as normalized:
                root = json.load(normalized.extractfile("index.json"))
            normalized_digests = tuple(
                sorted(str(descriptor["digest"]) for descriptor in root["manifests"])
            )
            assert all("annotations" not in descriptor for descriptor in root["manifests"])
        if command[-3:] == ["images", "list", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, "\n".join(plan.expected_refs) + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    report = MODULE.execute_import_plan(plan, run=run)

    assert report["status"] == "passed"
    assert report["canonical_base"] == plan.canonical_base
    assert report["normalized_archive_imported"] is True
    assert report["verified_descriptor_count"] == 3
    assert normalized_digests == plan.descriptor_digests
    assert commands[0][:-1] == plan.import_command[:-1]
    assert commands[0][-1] != plan.import_command[-1]
    assert commands[1] == ("ctr", "--namespace", "k8s.io", "images", "list", "--quiet")
    assert commands[2:] == [
        ("ctr", "--namespace", "k8s.io", "images", "inspect", reference)
        for reference in plan.expected_refs
    ]


def test_execute_import_fails_when_any_canonical_alias_is_missing(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, _ = _write_oci_archive(archive)
    plan = MODULE.prepare_import_plan(
        archive=archive,
        expected_sha256=archive_sha256,
        base_name="import-2026-08-17",
        containerd_cli="k3s",
    )

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-3:] == ["images", "list", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, "\n".join(plan.expected_refs[:-1]), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(MODULE.StagingOciImportError, match="canonical digest aliases"):
        MODULE.execute_import_plan(plan, run=run)


def test_execute_import_tags_and_verifies_the_exact_deployment_reference(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, expected_digests = _write_oci_archive(archive)
    root_digest = next(
        digest for digest in expected_digests if digest == MODULE.image_descriptors(archive)[1][0]
    )
    image_reference = f"ghcr.io/drew-z/enterprise-doc-api@{root_digest}"
    plan = MODULE.prepare_import_plan(
        archive=archive,
        expected_sha256=archive_sha256,
        base_name="import-2026-08-17",
        containerd_cli="ctr",
        image_reference=image_reference,
    )
    commands: list[tuple[str, ...]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        if command[-3:] == ["images", "list", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, "\n".join(plan.expected_refs) + "\n", "")
        if command[-3:] == ["images", "list", f"name=={image_reference}"]:
            table = (
                "REF TYPE DIGEST SIZE PLATFORMS LABELS\n"
                f"{image_reference} application/vnd.oci.image.index.v1+json "
                f"{root_digest} 1B linux/amd64 -\n"
            )
            return subprocess.CompletedProcess(command, 0, table, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    report = MODULE.execute_import_plan(plan, run=run)

    canonical_source = f"{plan.canonical_base}@{root_digest}"
    assert (
        "ctr",
        "--namespace",
        "k8s.io",
        "images",
        "tag",
        canonical_source,
        image_reference,
    ) in commands
    assert commands[-1] == (
        "ctr",
        "--namespace",
        "k8s.io",
        "images",
        "inspect",
        image_reference,
    )
    assert report["deployment_image_reference"] == image_reference


def test_import_plan_rejects_deployment_reference_for_a_nested_digest(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, expected_digests = _write_oci_archive(archive)
    root_digest = MODULE.image_descriptors(archive)[1][0]
    nested_digest = next(digest for digest in expected_digests if digest != root_digest)

    with pytest.raises(MODULE.StagingOciImportError, match="root OCI archive descriptor"):
        MODULE.prepare_import_plan(
            archive=archive,
            expected_sha256=archive_sha256,
            base_name="import-2026-08-17",
            containerd_cli="k3s",
            image_reference=f"ghcr.io/drew-z/enterprise-doc-api@{nested_digest}",
        )


@pytest.mark.parametrize(
    "base_name",
    ["docker.io/library/import", "Import-2026", "../import", "import:tag", ""],
)
def test_import_plan_rejects_ambiguous_or_unsafe_base_names(tmp_path: Path, base_name: str) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, _ = _write_oci_archive(archive)

    with pytest.raises(MODULE.StagingOciImportError, match="base name"):
        MODULE.prepare_import_plan(
            archive=archive,
            expected_sha256=archive_sha256,
            base_name=base_name,
            containerd_cli="k3s",
        )


def test_import_plan_rejects_checksum_mismatch_before_containerd(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    _write_oci_archive(archive)

    with pytest.raises(MODULE.StagingOciImportError, match="SHA-256"):
        MODULE.prepare_import_plan(
            archive=archive,
            expected_sha256="0" * 64,
            base_name="import-2026-08-17",
            containerd_cli="k3s",
        )


def test_cli_defaults_to_non_mutating_plan_without_requiring_k3s(tmp_path: Path) -> None:
    archive = tmp_path / "enterprise-doc-api.oci.tar"
    archive_sha256, _ = _write_oci_archive(archive)

    completed = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--archive",
            str(archive),
            "--expected-sha256",
            archive_sha256,
            "--base-name",
            "import-2026-08-17",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    assert report["status"] == "planned"
    assert report["mutation_performed"] is False
    assert report["confirm_required"] is True
    assert report["import_command"][:2] == ["k3s", "ctr"]
