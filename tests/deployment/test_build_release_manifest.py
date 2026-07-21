from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.build_release_manifest import (
    SERVICES,
    ReleaseManifestError,
    build_manifest,
)


def _write_fixture(root: Path, *, failed_service: str | None = None) -> None:
    for service in SERVICES:
        (root / f"image-{service}.digest.txt").write_text(
            f"registry.example/enterprise-doc-{service}@sha256:{hashlib.sha256(service.encode()).hexdigest()}\n",
            encoding="utf-8",
        )
        (root / f"release-step-outcomes-{service}.json").write_text(
            json.dumps(
                {
                    step: "failed" if service == failed_service and step == "verify" else "success"
                    for step in ("push", "scan", "sbom", "provenance", "sign", "verify")
                }
            ),
            encoding="utf-8",
        )
        names = (
            f"trivy-{service}.sarif",
            f"sbom-{service}.spdx.json",
            f"buildkit-provenance-{service}.json",
            f"buildkit-provenance-{service}.log",
            f"buildkit-provenance-{service}.type.txt",
            f"cosign-sign-attest-{service}.log",
            f"cosign-signature-verify-{service}.json",
            f"cosign-signature-verify-{service}.log",
            f"cosign-sbom-attestation-verify-{service}.json",
            f"cosign-sbom-attestation-verify-{service}.log",
            f"cosign-provenance-verify-{service}.json",
            f"cosign-provenance-verify-{service}.log",
        )
        for name in names:
            value = "slsaprovenance1\n" if name.endswith(".type.txt") else f"fixture:{name}\n"
            (root / name).write_text(value, encoding="utf-8")


def test_build_manifest_requires_all_four_successful_images(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    output = tmp_path / "release-manifest.json"
    manifest = build_manifest(
        tmp_path,
        repository="example/repo",
        ref="refs/tags/v1.2.3",
        commit_sha="a" * 40,
        run_id="42",
        run_attempt="1",
        workflow_ref="example/repo/.github/workflows/container.yml@refs/tags/v1.2.3",
        output=output,
    )
    assert manifest["status"] == "passed"
    assert set(manifest["images"]) == set(SERVICES)
    assert output.is_file()
    assert len(manifest["images"]["api"]["evidence"]) == 14


def test_build_manifest_rejects_failed_step(tmp_path: Path) -> None:
    _write_fixture(tmp_path, failed_service="web")
    with pytest.raises(ReleaseManifestError, match="verify"):
        build_manifest(
            tmp_path,
            repository="example/repo",
            ref="refs/tags/v1.2.3",
            commit_sha="a" * 40,
            run_id="42",
            run_attempt="1",
            workflow_ref="workflow",
            output=tmp_path / "release-manifest.json",
        )


def test_build_manifest_rejects_missing_evidence(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    (tmp_path / "sbom-api.spdx.json").unlink()
    with pytest.raises(ReleaseManifestError, match="missing api sbom evidence"):
        build_manifest(
            tmp_path,
            repository="example/repo",
            ref="refs/tags/v1.2.3",
            commit_sha="a" * 40,
            run_id="42",
            run_attempt="1",
            workflow_ref="workflow",
            output=tmp_path / "release-manifest.json",
        )
