from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from infra.cloudflare_mail import build as mail_build


@pytest.fixture
def source_snapshot(tmp_path: Path) -> tuple[Path, mail_build.SourceLock]:
    source = tmp_path / "source"
    content = {
        "worker/package.json": b'{"name":"fixture-worker"}\n',
        "frontend/index.html": b"<!doctype html><title>Local fixture</title>\n",
        "db/schema.sql": b"CREATE TABLE fixture (id INTEGER PRIMARY KEY);\n",
        "LICENSE": b"Synthetic source fixture, not upstream content.\n",
    }
    files: list[mail_build.SourceFile] = []
    for name, value in content.items():
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
        files.append(
            {
                "path": name,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
                "git_blob_sha1": "unused-in-this-sha256-fixture",
            }
        )
    lock: mail_build.SourceLock = {
        "repository": "local-fixture",
        "release": "fixture",
        "commit": "fixture",
        "archive_url": "https://example.invalid/unused",
        "archive_sha256": "unused",
        "archive_bytes": 0,
        "files": files,
    }
    return source, lock


def test_verified_source_is_copied_without_changing_original(
    tmp_path: Path, source_snapshot: tuple[Path, mail_build.SourceLock]
) -> None:
    source, lock = source_snapshot
    work = tmp_path / "work"
    work.mkdir()
    copied = mail_build.prepare_source(work, source, lock)
    for item in lock["files"]:
        assert (copied / item["path"]).read_bytes() == (source / item["path"]).read_bytes()
    (copied / "frontend/index.html").write_bytes(b"build-only override")
    mail_build.verify_source(source, lock)


@pytest.mark.parametrize("change", ["same_size_tampering", "missing_file", "extra_file"])
def test_source_changes_are_rejected(
    source_snapshot: tuple[Path, mail_build.SourceLock], change: str
) -> None:
    source, lock = source_snapshot
    selected = source / "worker/package.json"
    if change == "same_size_tampering":
        selected.write_bytes(selected.read_bytes().replace(b"fixture", b"changed"))
    elif change == "missing_file":
        selected.unlink()
    else:
        (source / "worker/unpinned.js").write_text("unapproved build input", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Source file set|Pinned source mismatch"):
        mail_build.verify_source(source, lock)


@pytest.mark.parametrize("existing", ["work", "output"])
def test_existing_directories_are_never_overwritten(tmp_path: Path, existing: str) -> None:
    work, output = tmp_path / "work", tmp_path / "output"
    protected = work if existing == "work" else output
    protected.mkdir()
    marker = protected / "owned.txt"
    marker.write_text("keep this data", encoding="utf-8")
    with pytest.raises(ValueError, match="nothing was overwritten"):
        mail_build.build(None, work, output)
    assert marker.read_text("utf-8") == "keep this data"
    assert not (output if existing == "work" else work).exists()


@pytest.mark.parametrize("work_inside_output", [False, True])
def test_nested_build_directories_are_rejected_before_creation(
    tmp_path: Path, work_inside_output: bool
) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    work, output = (inner, outer) if work_inside_output else (outer, inner)
    with pytest.raises(ValueError, match="must be separate"):
        mail_build.build(None, work, output)
    assert not outer.exists()


def test_bad_source_is_rejected_before_creating_build_directories(
    tmp_path: Path,
    source_snapshot: tuple[Path, mail_build.SourceLock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, lock = source_snapshot
    (source / "LICENSE").write_text("changed source", encoding="utf-8")
    monkeypatch.setattr(mail_build, "read_lock", lambda: lock)
    work, output = tmp_path / "work", tmp_path / "output"
    with pytest.raises(ValueError, match="Pinned source mismatch"):
        mail_build.build(source, work, output)
    assert not work.exists()
    assert not output.exists()


def test_child_environment_does_not_inherit_service_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential_keys = [
        "CLOUDFLARE_API_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "BROWSER_AUTH__CLIENT_SECRET",
        "NPM_TOKEN",
    ]
    for key in credential_keys:
        monkeypatch.setenv(key, "local-credential-canary")
    child = mail_build.local_environment(tmp_path)
    assert all(key not in child for key in credential_keys)
    assert "local-credential-canary" not in child.values()
    assert Path(child["NPM_CONFIG_USERCONFIG"]).read_bytes() == b""
    assert Path(child["NPM_CONFIG_GLOBALCONFIG"]).read_bytes() == b""
    assert child["CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV"] == "false"
