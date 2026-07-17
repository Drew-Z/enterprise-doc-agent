from __future__ import annotations

import json
import sys
from argparse import Namespace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.uploads import UploadCleanupReport

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "cleanup_uploads.py"
_SPEC = spec_from_file_location("cleanup_uploads_test_module", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
cleanup_uploads = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cleanup_uploads
_SPEC.loader.exec_module(cleanup_uploads)


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "dry_run": False,
        "batch_size": None,
        "expiry_grace_seconds": None,
        "completing_grace_seconds": None,
        "orphan_grace_seconds": None,
        "claim_ttl_seconds": None,
    }
    values.update(overrides)
    return Namespace(**values)


def test_cleanup_overrides_are_revalidated_without_replacing_other_settings() -> None:
    base = UploadSettings(cleanup_batch_size=25, cleanup_orphan_grace_seconds=7200)

    updated = cleanup_uploads._validated_upload_settings(
        settings=base,
        args=_args(batch_size=7),
    )

    assert updated.cleanup_batch_size == 7
    assert updated.cleanup_orphan_grace_seconds == 7200
    assert base.cleanup_batch_size == 25


def test_cleanup_override_outside_typed_range_is_an_argument_error() -> None:
    with pytest.raises(cleanup_uploads.CleanupArgumentError):
        cleanup_uploads._validated_upload_settings(
            settings=UploadSettings(),
            args=_args(batch_size=0),
        )


def test_cleanup_main_preserves_argparse_exit_two_for_invalid_override() -> None:
    with pytest.raises(SystemExit) as raised:
        cleanup_uploads.main(["--batch-size", "0"])

    assert raised.value.code == 2


def test_cleanup_main_prints_one_compact_json_line_and_returns_report_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(args: Namespace) -> UploadCleanupReport:
        assert args.dry_run is True
        return UploadCleanupReport.empty(dry_run=True)

    monkeypatch.setattr(cleanup_uploads, "_run", fake_run)

    exit_code = cleanup_uploads.main(["--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.count("\n") == 1
    assert " " not in output
    assert json.loads(output) == UploadCleanupReport.empty(dry_run=True).to_dict()


def test_cleanup_main_returns_one_without_printing_exception_messages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(args: Namespace) -> UploadCleanupReport:
        return UploadCleanupReport.empty(dry_run=args.dry_run).with_exception(
            RuntimeError("secret endpoint and object key")
        )

    monkeypatch.setattr(cleanup_uploads, "_run", fake_run)

    exit_code = cleanup_uploads.main([])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "secret" not in output
    assert json.loads(output)["exceptionsByClass"] == {"RuntimeError": 1}


@pytest.mark.asyncio
async def test_cleanup_run_passes_dry_run_and_closes_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    settings = ApiSettings(_env_file=None)

    class FakeEngine:
        async def dispose(self) -> None:
            events.append("engine.dispose")

    class FakeStore:
        async def close(self) -> None:
            events.append("store.close")

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["settings"].cleanup_batch_size == 3

        async def run(self, *, dry_run: bool) -> UploadCleanupReport:
            events.append(f"run:{dry_run}")
            return UploadCleanupReport.empty(dry_run=dry_run)

    engine = FakeEngine()
    store = FakeStore()
    monkeypatch.setattr(cleanup_uploads, "ApiSettings", lambda: settings)
    monkeypatch.setattr(cleanup_uploads, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cleanup_uploads, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(
        cleanup_uploads,
        "Boto3MultipartObjectStore",
        lambda *, settings: store,
    )
    monkeypatch.setattr(cleanup_uploads, "UploadCleanupService", FakeService)

    report = await cleanup_uploads._run(_args(dry_run=True, batch_size=3))

    assert report.failed is False
    assert events == ["run:True", "store.close", "engine.dispose"]


@pytest.mark.asyncio
async def test_cleanup_run_disposes_engine_when_store_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    settings = ApiSettings(_env_file=None)

    class FakeEngine:
        async def dispose(self) -> None:
            events.append("engine.dispose")

    class FakeStore:
        async def close(self) -> None:
            events.append("store.close")
            raise ConnectionError("sensitive close failure")

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, *, dry_run: bool) -> UploadCleanupReport:
            return UploadCleanupReport.empty(dry_run=dry_run)

    engine = FakeEngine()
    store = FakeStore()
    monkeypatch.setattr(cleanup_uploads, "ApiSettings", lambda: settings)
    monkeypatch.setattr(cleanup_uploads, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(cleanup_uploads, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(
        cleanup_uploads,
        "Boto3MultipartObjectStore",
        lambda *, settings: store,
    )
    monkeypatch.setattr(cleanup_uploads, "UploadCleanupService", FakeService)

    report = await cleanup_uploads._run(_args())

    assert report.failed is True
    assert report.exceptions_by_class == {"ConnectionError": 1}
    assert events == ["store.close", "engine.dispose"]
