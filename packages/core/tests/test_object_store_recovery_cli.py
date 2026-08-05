from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import enterprise_doc_core.recovery.remap_cli as remap_cli
import enterprise_doc_core.recovery.restore_cli as restore_cli
import enterprise_doc_core.recovery.snapshot_cli as snapshot_cli
from enterprise_doc_core.recovery.object_store import SnapshotManifest, SnapshotResult


def _raise_secret_error(_: object) -> object:
    raise RuntimeError("super-secret endpoint and object key")


def test_snapshot_cli_is_dry_run_by_default_and_redacts_failures(
    monkeypatch: object,
    capsys: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(snapshot_cli, "_execute", _raise_secret_error)

    exit_code = snapshot_cli.main(
        [
            "--drill-id",
            "20260806-staging",
            "--expected-endpoint-host",
            "account.r2.cloudflarestorage.com",
            "--allowed-bucket",
            "documents",
            "--allowed-bucket",
            "artifacts",
            "--manifest-path",
            str(tmp_path / "manifest.json"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "super-secret" not in output
    assert json.loads(output) == {
        "error_class": "RuntimeError",
        "operation": "r2-object-snapshot",
        "status": "failed",
    }


def test_restore_cli_is_dry_run_by_default_and_redacts_failures(
    monkeypatch: object,
    capsys: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(restore_cli, "_execute", _raise_secret_error)

    exit_code = restore_cli.main(
        [
            "--manifest-path",
            str(tmp_path / "manifest.json"),
            "--expected-manifest-sha256",
            "0" * 64,
            "--restore-id",
            "20260806-staging",
            "--expected-database-name",
            "enterprise_doc_restore_20260805t094423z",
            "--expected-endpoint-host",
            "account.r2.cloudflarestorage.com",
            "--allowed-bucket",
            "documents",
            "--allowed-bucket",
            "artifacts",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "super-secret" not in output
    assert json.loads(output) == {
        "error_class": "RuntimeError",
        "operation": "r2-object-restore",
        "status": "failed",
    }


def test_remap_cli_is_dry_run_by_default_and_redacts_failures(
    monkeypatch: object,
    capsys: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(remap_cli, "_execute", _raise_secret_error)

    exit_code = remap_cli.main(
        [
            "--manifest-path",
            str(tmp_path / "manifest.json"),
            "--expected-manifest-sha256",
            "0" * 64,
            "--restore-id",
            "20260806-staging",
            "--expected-database-name",
            "enterprise_doc_restore_20260805t094423z",
            "--expected-endpoint-host",
            "account.r2.cloudflarestorage.com",
            "--allowed-bucket",
            "documents",
            "--allowed-bucket",
            "artifacts",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "super-secret" not in output
    assert json.loads(output) == {
        "error_class": "RuntimeError",
        "operation": "r2-object-reference-remap",
        "status": "failed",
    }


def test_snapshot_cli_writes_private_manifest_only_after_confirmation(
    monkeypatch: object,
    capsys: object,
    tmp_path: Path,
) -> None:
    manifest = SnapshotManifest(
        schema_version=1,
        operation="r2-object-snapshot",
        drill_id="20260806-staging",
        created_at="2026-08-06T00:00:00+00:00",
        endpoint_host="account.r2.cloudflarestorage.com",
        snapshot_prefix="enterprise-doc-recovery/snapshots/20260806-staging",
        manifest_bucket="documents",
        manifest_key="enterprise-doc-recovery/snapshots/20260806-staging/manifest.json",
        objects=(),
        manifest_sha256="a" * 64,
    )
    client = SimpleNamespace(close=lambda: None)
    settings = SimpleNamespace(
        object_store=SimpleNamespace(
            endpoint="https://account.r2.cloudflarestorage.com",
            documents_bucket="documents",
        )
    )
    monkeypatch.setattr(snapshot_cli, "FoundationSettings", lambda: settings)
    monkeypatch.setattr(snapshot_cli, "run_reference_load", lambda _: ())
    monkeypatch.setattr(snapshot_cli, "create_s3_client", lambda *_args, **_kwargs: client)

    def fake_snapshot(**kwargs: object) -> SnapshotResult:
        confirmed = bool(kwargs["confirm"])
        return SnapshotResult(
            status="passed" if confirmed else "planned",
            object_count=0,
            copied_count=0,
            existing_count=0,
            manifest=manifest,
        )

    monkeypatch.setattr(snapshot_cli, "create_snapshot", fake_snapshot)
    manifest_path = tmp_path / "manifest.json"
    base_args = [
        "--drill-id",
        "20260806-staging",
        "--expected-endpoint-host",
        "account.r2.cloudflarestorage.com",
        "--allowed-bucket",
        "documents",
        "--manifest-path",
        str(manifest_path),
    ]

    assert snapshot_cli.main(base_args) == 0
    assert not manifest_path.exists()
    assert json.loads(capsys.readouterr().out)["confirmed"] is False

    assert snapshot_cli.main([*base_args, "--confirm"]) == 0
    assert manifest_path.read_bytes() == manifest.render()
    if os.name != "nt":
        assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert json.loads(capsys.readouterr().out)["confirmed"] is True

    changed = replace(manifest, created_at="2026-08-06T00:01:00+00:00")
    monkeypatch.setattr(
        snapshot_cli,
        "create_snapshot",
        lambda **_: SnapshotResult(
            status="passed",
            object_count=0,
            copied_count=0,
            existing_count=0,
            manifest=changed,
        ),
    )
    assert snapshot_cli.main([*base_args, "--confirm"]) == 1
    assert manifest_path.read_bytes() == manifest.render()
