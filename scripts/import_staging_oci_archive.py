from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

CONTAINERD_NAMESPACE = "k8s.io"
_BASE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
    r"@sha256:[0-9a-f]{64}$"
)
_NORMALIZED_ARCHIVE_PLACEHOLDER = "<normalized-archive>"
_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
_IMAGE_MEDIA_TYPES = _INDEX_MEDIA_TYPES | _MANIFEST_MEDIA_TYPES
_MAX_DESCRIPTOR_JSON_BYTES = 16 * 1024 * 1024


class StagingOciImportError(ValueError):
    """Raised when an OCI relay archive cannot be imported without ambiguity."""


class OciImportPlan(NamedTuple):
    archive: Path
    archive_sha256: str
    canonical_base: str
    root_descriptor_digests: tuple[str, ...]
    descriptor_digests: tuple[str, ...]
    expected_refs: tuple[str, ...]
    image_reference: str | None
    containerd_prefix: tuple[str, ...]
    import_command: tuple[str, ...]


class OciImageDescriptor(NamedTuple):
    media_type: str
    digest: str
    size: int


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise StagingOciImportError("unable to read OCI archive") from error
    return digest.hexdigest()


def _containerd_prefix(containerd_cli: str) -> tuple[str, ...]:
    candidate = containerd_cli.strip()
    if not candidate or any(character.isspace() for character in candidate):
        raise StagingOciImportError("containerd CLI must be one executable path")
    executable_name = Path(candidate).name
    if executable_name == "k3s":
        return (candidate, "ctr")
    if executable_name == "ctr":
        return (candidate,)
    raise StagingOciImportError("containerd CLI executable must be k3s or ctr")


def _read_member(archive: tarfile.TarFile, name: str) -> bytes:
    member = None
    for candidate in (name, f"./{name}"):
        try:
            member = archive.getmember(candidate)
            break
        except KeyError:
            continue
    if member is None:
        raise StagingOciImportError(f"OCI archive is missing required member {name}")
    if not member.isfile() or member.size > _MAX_DESCRIPTOR_JSON_BYTES:
        raise StagingOciImportError(f"OCI archive member {name} is not bounded JSON")
    source = archive.extractfile(member)
    if source is None:
        raise StagingOciImportError(f"OCI archive member {name} could not be read")
    content = source.read(_MAX_DESCRIPTOR_JSON_BYTES + 1)
    if len(content) != member.size:
        raise StagingOciImportError(f"OCI archive member {name} changed size while reading")
    return content


def _decode_json(content: bytes, description: str) -> dict[str, Any]:
    try:
        payload: Any = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StagingOciImportError(f"{description} is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 2:
        raise StagingOciImportError(f"{description} is not an OCI schemaVersion 2 object")
    return payload


def _required_descriptors(payload: dict[str, Any], description: str) -> list[dict[str, Any]]:
    manifests = payload.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise StagingOciImportError(f"{description} does not contain image descriptors")
    descriptors: list[dict[str, Any]] = []
    for value in manifests:
        if not isinstance(value, dict):
            raise StagingOciImportError(f"{description} contains a non-object descriptor")
        descriptors.append(value)
    return descriptors


def image_descriptors(
    path: Path,
) -> tuple[tuple[OciImageDescriptor, ...], tuple[str, ...]]:
    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError) as error:
        raise StagingOciImportError("unable to open OCI archive") from error
    with archive:
        root = _decode_json(_read_member(archive, "index.json"), "OCI archive index")
        root_descriptors = _required_descriptors(root, "OCI archive index")
        pending = list(root_descriptors)
        observed: dict[str, OciImageDescriptor] = {}
        root_digests: list[str] = []
        while pending:
            descriptor = pending.pop()
            media_type = descriptor.get("mediaType")
            digest = descriptor.get("digest")
            size = descriptor.get("size")
            if media_type not in _IMAGE_MEDIA_TYPES:
                continue
            if not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None:
                raise StagingOciImportError("OCI image descriptor has an invalid digest")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise StagingOciImportError("OCI image descriptor has an invalid size")
            normalized = OciImageDescriptor(media_type=media_type, digest=digest, size=size)
            previous = observed.get(digest)
            if previous is not None:
                if previous != normalized:
                    raise StagingOciImportError(
                        "OCI image descriptor metadata is inconsistent for one digest"
                    )
                continue
            observed[digest] = normalized
            encoded_digest = digest.removeprefix("sha256:")
            content = _read_member(archive, f"blobs/sha256/{encoded_digest}")
            if len(content) != size or hashlib.sha256(content).hexdigest() != encoded_digest:
                raise StagingOciImportError("OCI image descriptor content failed digest validation")
            decoded = _decode_json(content, f"OCI image descriptor {digest}")
            if media_type in _INDEX_MEDIA_TYPES:
                pending.extend(_required_descriptors(decoded, f"OCI image index {digest}"))
        if not observed:
            raise StagingOciImportError(
                "OCI archive contains no image index or manifest descriptors"
            )
        for descriptor in root_descriptors:
            digest = descriptor.get("digest")
            if isinstance(digest, str) and digest in observed:
                root_digests.append(digest)
        return tuple(observed[digest] for digest in sorted(observed)), tuple(
            sorted(set(root_digests))
        )


def image_descriptor_digests(path: Path) -> tuple[str, ...]:
    descriptors, _ = image_descriptors(path)
    return tuple(descriptor.digest for descriptor in descriptors)


def _write_normalized_archive(
    source_path: Path,
    target_path: Path,
    descriptors: tuple[OciImageDescriptor, ...],
) -> None:
    normalized_index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": descriptor.media_type,
                    "digest": descriptor.digest,
                    "size": descriptor.size,
                }
                for descriptor in descriptors
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    try:
        source = tarfile.open(source_path, mode="r:*")
    except (OSError, tarfile.TarError) as error:
        raise StagingOciImportError("unable to reopen validated OCI archive") from error
    try:
        with source, tarfile.open(target_path, mode="w") as target:
            index_count = 0
            for member in source.getmembers():
                cloned = copy.copy(member)
                normalized_name = member.name.removeprefix("./")
                if normalized_name == "index.json":
                    index_count += 1
                    cloned.name = "index.json"
                    cloned.size = len(normalized_index)
                    cloned.mtime = 0
                    cloned.pax_headers = {}
                    target.addfile(cloned, io.BytesIO(normalized_index))
                    continue
                body = source.extractfile(member) if member.isfile() else None
                target.addfile(cloned, body)
            if index_count != 1:
                raise StagingOciImportError(
                    "OCI archive must contain exactly one root index.json member"
                )
    except (OSError, tarfile.TarError) as error:
        raise StagingOciImportError("unable to normalize validated OCI archive") from error


def prepare_import_plan(
    *,
    archive: Path,
    expected_sha256: str,
    base_name: str,
    containerd_cli: str,
    image_reference: str | None = None,
) -> OciImportPlan:
    if _BASE_NAME_PATTERN.fullmatch(base_name) is None:
        raise StagingOciImportError(
            "base name must be a lowercase short name without registry, tag, digest, or path"
        )
    normalized_sha256 = expected_sha256.lower()
    if _SHA256_PATTERN.fullmatch(normalized_sha256) is None:
        raise StagingOciImportError("expected archive SHA-256 must be 64 hexadecimal characters")
    actual_sha256 = _sha256_file(archive)
    if actual_sha256 != normalized_sha256:
        raise StagingOciImportError("OCI archive SHA-256 does not match expectation")
    descriptors, root_descriptor_digests = image_descriptors(archive)
    descriptor_digests = tuple(descriptor.digest for descriptor in descriptors)
    normalized_image_reference = image_reference.strip() if image_reference is not None else None
    if normalized_image_reference == "":
        normalized_image_reference = None
    if normalized_image_reference is not None:
        if _IMAGE_REFERENCE_PATTERN.fullmatch(normalized_image_reference) is None:
            raise StagingOciImportError(
                "image reference must be a lowercase registry path with an immutable SHA-256"
            )
        reference_digest = normalized_image_reference.rsplit("@", 1)[1]
        if reference_digest not in root_descriptor_digests:
            raise StagingOciImportError(
                "image reference digest must identify a root OCI archive descriptor"
            )
    canonical_base = f"docker.io/library/{base_name}"
    prefix = _containerd_prefix(containerd_cli)
    expected_refs = tuple(f"{canonical_base}@{digest}" for digest in descriptor_digests)
    import_command = (
        *prefix,
        "--namespace",
        CONTAINERD_NAMESPACE,
        "images",
        "import",
        "--all-platforms",
        "--digests",
        "--base-name",
        canonical_base,
        _NORMALIZED_ARCHIVE_PLACEHOLDER,
    )
    return OciImportPlan(
        archive=archive,
        archive_sha256=actual_sha256,
        canonical_base=canonical_base,
        root_descriptor_digests=root_descriptor_digests,
        descriptor_digests=descriptor_digests,
        expected_refs=expected_refs,
        image_reference=normalized_image_reference,
        containerd_prefix=prefix,
        import_command=import_command,
    )


def _run_checked(
    run: RunCommand,
    command: tuple[str, ...],
    *,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(list(command), check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise StagingOciImportError(f"{operation} requires the containerd CLI") from error
    except subprocess.CalledProcessError as error:
        raise StagingOciImportError(
            f"{operation} failed with exit status {error.returncode}"
        ) from error
    except OSError as error:
        raise StagingOciImportError(f"{operation} could not start") from error


def execute_import_plan(
    plan: OciImportPlan,
    *,
    run: RunCommand = subprocess.run,
) -> dict[str, object]:
    descriptors, root_descriptor_digests = image_descriptors(plan.archive)
    if tuple(descriptor.digest for descriptor in descriptors) != plan.descriptor_digests:
        raise StagingOciImportError("validated OCI descriptor set changed before import")
    if root_descriptor_digests != plan.root_descriptor_digests:
        raise StagingOciImportError("validated OCI root descriptor set changed before import")
    with tempfile.TemporaryDirectory(prefix="enterprise-doc-oci-import-") as temporary_dir:
        normalized_archive = Path(temporary_dir) / plan.archive.name
        _write_normalized_archive(plan.archive, normalized_archive, descriptors)
        import_command = (*plan.import_command[:-1], str(normalized_archive))
        _run_checked(run, import_command, operation="containerd OCI import")
    list_command = (
        *plan.containerd_prefix,
        "--namespace",
        CONTAINERD_NAMESPACE,
        "images",
        "list",
        "--quiet",
    )
    listed = _run_checked(run, list_command, operation="containerd image listing")
    observed_refs = {line.strip() for line in listed.stdout.splitlines() if line.strip()}
    missing = sorted(set(plan.expected_refs) - observed_refs)
    if missing:
        raise StagingOciImportError(
            "canonical digest aliases are missing after import: "
            f"{len(missing)} of {len(plan.expected_refs)}"
        )
    for reference in plan.expected_refs:
        inspect_command = (
            *plan.containerd_prefix,
            "--namespace",
            CONTAINERD_NAMESPACE,
            "images",
            "inspect",
            reference,
        )
        _run_checked(run, inspect_command, operation="containerd canonical image inspection")
    if plan.image_reference is not None:
        image_reference_digest = plan.image_reference.rsplit("@", 1)[1]
        canonical_source = f"{plan.canonical_base}@{image_reference_digest}"
        if plan.image_reference not in observed_refs:
            tag_command = (
                *plan.containerd_prefix,
                "--namespace",
                CONTAINERD_NAMESPACE,
                "images",
                "tag",
                canonical_source,
                plan.image_reference,
            )
            _run_checked(run, tag_command, operation="containerd deployment image tagging")
        target_list_command = (
            *plan.containerd_prefix,
            "--namespace",
            CONTAINERD_NAMESPACE,
            "images",
            "list",
            f"name=={plan.image_reference}",
        )
        target_listed = _run_checked(
            run,
            target_list_command,
            operation="containerd deployment image verification",
        )
        target_rows = [
            line.split() for line in target_listed.stdout.splitlines()[1:] if line.strip()
        ]
        if (
            len(target_rows) != 1
            or len(target_rows[0]) < 3
            or target_rows[0][0] != plan.image_reference
            or target_rows[0][2] != image_reference_digest
        ):
            raise StagingOciImportError(
                "deployment image reference does not target the expected root digest"
            )
        deployment_inspect_command = (
            *plan.containerd_prefix,
            "--namespace",
            CONTAINERD_NAMESPACE,
            "images",
            "inspect",
            plan.image_reference,
        )
        _run_checked(
            run,
            deployment_inspect_command,
            operation="containerd deployment image inspection",
        )
    return {
        "schema_version": 1,
        "operation": "staging-oci-archive-import",
        "status": "passed",
        "archive_sha256": plan.archive_sha256,
        "containerd_namespace": CONTAINERD_NAMESPACE,
        "canonical_base": plan.canonical_base,
        "normalized_archive_imported": True,
        "verified_descriptor_count": len(plan.expected_refs),
        "verified_refs": list(plan.expected_refs),
        "deployment_image_reference": plan.image_reference,
    }


def _planned_report(plan: OciImportPlan) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "staging-oci-archive-import",
        "status": "planned",
        "mutation_performed": False,
        "confirm_required": True,
        "archive_sha256": plan.archive_sha256,
        "containerd_namespace": CONTAINERD_NAMESPACE,
        "canonical_base": plan.canonical_base,
        "normalized_archive_import": True,
        "descriptor_count": len(plan.expected_refs),
        "expected_refs": list(plan.expected_refs),
        "deployment_image_reference": plan.image_reference,
        "source_archive": str(plan.archive),
        "import_command": list(plan.import_command),
    }


def _write_report(path: Path | None, report: dict[str, object]) -> None:
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and import one staging OCI relay archive with canonical containerd aliases"
        )
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--base-name", required=True)
    parser.add_argument("--image-reference")
    parser.add_argument("--containerd-cli", default="k3s")
    parser.add_argument("--record-path", type=Path)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        plan = prepare_import_plan(
            archive=args.archive,
            expected_sha256=args.expected_sha256,
            base_name=args.base_name,
            containerd_cli=args.containerd_cli,
            image_reference=args.image_reference,
        )
    except StagingOciImportError as error:
        raise SystemExit(str(error)) from error
    if not args.confirm:
        _write_report(args.record_path, _planned_report(plan))
        return
    if shutil.which(plan.containerd_prefix[0]) is None:
        raise SystemExit("containerd CLI executable is not available")
    try:
        report = execute_import_plan(plan)
    except StagingOciImportError as error:
        failed_report = {
            "schema_version": 1,
            "operation": "staging-oci-archive-import",
            "status": "failed",
            "archive_sha256": plan.archive_sha256,
            "containerd_namespace": CONTAINERD_NAMESPACE,
            "canonical_base": plan.canonical_base,
            "expected_descriptor_count": len(plan.expected_refs),
            "error": str(error),
        }
        _write_report(args.record_path, failed_report)
        raise SystemExit("staging OCI archive import failed") from error
    _write_report(args.record_path, report)


if __name__ == "__main__":
    main()
