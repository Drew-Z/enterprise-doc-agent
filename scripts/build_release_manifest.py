from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SERVICES = ("api", "worker", "consumer", "web")
DIGEST_LINE = re.compile(r"^(?P<image>[^\s@]+)@(?P<digest>sha256:[0-9a-f]{64})$")
REQUIRED_FILES = {
    "digest": "image-{service}.digest.txt",
    "trivy": "trivy-{service}.sarif",
    "sbom": "sbom-{service}.spdx.json",
    "provenance": "buildkit-provenance-{service}.json",
    "provenance_log": "buildkit-provenance-{service}.log",
    "provenance_type": "buildkit-provenance-{service}.type.txt",
    "sign_log": "cosign-sign-attest-{service}.log",
    "signature": "cosign-signature-verify-{service}.json",
    "signature_log": "cosign-signature-verify-{service}.log",
    "sbom_attestation": "cosign-sbom-attestation-verify-{service}.json",
    "sbom_attestation_log": "cosign-sbom-attestation-verify-{service}.log",
    "provenance_attestation": "cosign-provenance-verify-{service}.json",
    "provenance_attestation_log": "cosign-provenance-verify-{service}.log",
    "outcomes": "release-step-outcomes-{service}.json",
}
SUCCESS_STEPS = ("push", "scan", "sbom", "provenance", "sign", "verify")
PROVENANCE_TYPES = {"slsaprovenance02", "slsaprovenance1"}


class ReleaseManifestError(ValueError):
    """Raised when a release evidence set is incomplete or inconsistent."""


def _non_empty_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise ReleaseManifestError(f"missing {label}: {path}")
    if path.stat().st_size == 0:
        raise ReleaseManifestError(f"empty {label}: {path}")
    return path


def _read_digest(path: Path) -> tuple[str, str]:
    line = _non_empty_file(path, "digest evidence").read_text(encoding="utf-8").strip()
    match = DIGEST_LINE.fullmatch(line)
    if match is None:
        raise ReleaseManifestError(f"invalid immutable digest line in {path}")
    return match.group("image"), match.group("digest")


def _read_outcomes(path: Path) -> dict[str, str]:
    try:
        value = json.loads(_non_empty_file(path, "release outcomes").read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReleaseManifestError(f"invalid release outcomes JSON: {path}") from error
    if not isinstance(value, dict):
        raise ReleaseManifestError(f"release outcomes must be an object: {path}")
    for step in SUCCESS_STEPS:
        if value.get(step) != "success":
            raise ReleaseManifestError(
                f"release step {step!r} is not successful for {path}: {value.get(step)!r}"
            )
    return {step: str(value[step]) for step in SUCCESS_STEPS}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_provenance_type(path: Path) -> str:
    value = _non_empty_file(path, "provenance type evidence").read_text(encoding="utf-8").strip()
    if value not in PROVENANCE_TYPES:
        raise ReleaseManifestError(f"unsupported provenance predicate type in {path}: {value!r}")
    return value


def build_manifest(
    evidence_root: Path,
    *,
    repository: str,
    ref: str,
    commit_sha: str,
    run_id: str,
    run_attempt: str,
    workflow_ref: str,
    output: Path,
) -> dict[str, Any]:
    if not all((repository, ref, commit_sha, run_id, run_attempt, workflow_ref)):
        raise ReleaseManifestError(
            "repository, ref, commit_sha, run_id, run_attempt and workflow_ref are required"
        )
    images: dict[str, Any] = {}
    for service in SERVICES:
        files = {
            key: evidence_root / filename.format(service=service)
            for key, filename in REQUIRED_FILES.items()
        }
        image, digest = _read_digest(files["digest"])
        outcomes = _read_outcomes(files["outcomes"])
        provenance_type = _read_provenance_type(files["provenance_type"])
        evidence: dict[str, dict[str, str]] = {}
        for key, path in files.items():
            _non_empty_file(path, f"{service} {key} evidence")
            evidence[key] = {"path": path.as_posix(), "sha256": _sha256(path)}
        images[service] = {
            "image": image,
            "digest": digest,
            "steps": outcomes,
            "provenance_predicate_type": provenance_type,
            "evidence": evidence,
        }

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "release": {
            "repository": repository,
            "ref": ref,
            "commit_sha": commit_sha,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "workflow_ref": workflow_ref,
        },
        "images": images,
        "limitations": [
            "This manifest proves CI evidence completeness for immutable image digests; "
            "it does not prove staging or production deployment.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a strict four-image release evidence manifest"
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_manifest(
            args.evidence_root,
            repository=args.repository,
            ref=args.ref,
            commit_sha=args.commit_sha,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_ref=args.workflow_ref,
            output=args.output,
        )
    except (OSError, ReleaseManifestError) as error:
        raise SystemExit(str(error)) from error
    print(
        json.dumps(
            {"status": manifest["status"], "images": sorted(manifest["images"])},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
