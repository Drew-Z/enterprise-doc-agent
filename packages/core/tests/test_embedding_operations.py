from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.documents import embedding_probe, embedding_rollout


def _probe_report(status: str = "passed") -> dict[str, object]:
    return {
        "status": status,
        "provider": "openai_compatible",
        "model": "Qwen/Qwen3-Embedding-4B",
        "dimension": 1024,
        "version": 2,
        "item_count": 2,
        "finite": True,
        "nonzero_norms": status == "passed",
        "elapsed_ms": 1.0,
        "values_redacted": True,
    }


def _reindex_report(
    *,
    selected: int,
    created: int = 0,
    replayed: int = 0,
    apply: bool = False,
) -> dict[str, object]:
    return {
        "status": "applied" if apply else "planned",
        "selected": selected,
        "created": created,
        "replayed": replayed,
        "embedding_model": "Qwen/Qwen3-Embedding-4B",
        "embedding_dimension": 1024,
        "embedding_version": 2,
        "values_redacted": True,
    }


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
async def test_embedding_rollout_passes_without_reindex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        return _probe_report()

    async def fake_reindex(*args: Any, **kwargs: Any) -> dict[str, object]:
        del args, kwargs
        return _reindex_report(selected=0)

    monkeypatch.setattr(embedding_rollout, "probe", fake_probe)
    monkeypatch.setattr(embedding_rollout, "enqueue_reindex", fake_reindex)

    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=10,
        poll_seconds=1,
    )

    assert report["status"] == "passed"
    assert report["values_redacted"] is True
    assert report["reindex"] == {
        "status": "completed",
        "initial_plan": _reindex_report(selected=0),
        "attempts": [],
        "final_plan": _reindex_report(selected=0),
    }


@pytest.mark.asyncio
async def test_embedding_rollout_replays_safely_until_plan_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = iter(
        (
            _reindex_report(selected=1),
            _reindex_report(selected=1, replayed=1, apply=True),
            _reindex_report(selected=0),
        )
    )

    async def fake_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        return _probe_report()

    async def fake_reindex(*args: Any, **kwargs: Any) -> dict[str, object]:
        del args, kwargs
        return next(reports)

    clock = _Clock()
    monkeypatch.setattr(embedding_rollout, "probe", fake_probe)
    monkeypatch.setattr(embedding_rollout, "enqueue_reindex", fake_reindex)

    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=10,
        poll_seconds=1,
        sleep=clock.sleep,
        clock=clock,
    )

    assert report["status"] == "passed"
    assert report["reindex"]["attempts"] == [_reindex_report(selected=1, replayed=1, apply=True)]
    assert report["reindex"]["final_plan"]["selected"] == 0


@pytest.mark.asyncio
async def test_embedding_rollout_fails_closed_when_drain_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        return _probe_report()

    async def fake_reindex(*args: Any, **kwargs: Any) -> dict[str, object]:
        apply = bool(kwargs["apply"])
        return _reindex_report(selected=1, replayed=1 if apply else 0, apply=apply)

    clock = _Clock()
    monkeypatch.setattr(embedding_rollout, "probe", fake_probe)
    monkeypatch.setattr(embedding_rollout, "enqueue_reindex", fake_reindex)

    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=1,
        poll_seconds=1,
        sleep=clock.sleep,
        clock=clock,
    )

    assert report["status"] == "failed"
    assert report["failed_stage"] == "reindex_drain"
    assert report["error_code"] == "embedding_reindex_timed_out"
    assert report["reindex"]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_embedding_rollout_rejects_inconsistent_apply_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = iter(
        (
            _reindex_report(selected=2),
            _reindex_report(selected=2, created=1, apply=True),
        )
    )

    async def fake_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        return _probe_report()

    async def fake_reindex(*args: Any, **kwargs: Any) -> dict[str, object]:
        del args, kwargs
        return next(reports)

    monkeypatch.setattr(embedding_rollout, "probe", fake_probe)
    monkeypatch.setattr(embedding_rollout, "enqueue_reindex", fake_reindex)

    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=10,
        poll_seconds=1,
    )

    assert report["status"] == "failed"
    assert report["error_code"] == "embedding_reindex_count_mismatch"


@pytest.mark.asyncio
async def test_embedding_rollout_redacts_operation_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        raise RuntimeError("postgresql://user:secret@example.invalid/database")

    monkeypatch.setattr(embedding_rollout, "probe", failing_probe)
    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=10,
        poll_seconds=1,
    )

    assert report["status"] == "failed"
    assert report["error_code"] == "embedding_rollout_operation_failed"
    assert "secret" not in str(report)


@pytest.mark.asyncio
async def test_embedding_rollout_times_out_a_hung_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hanging_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        await asyncio.Event().wait()
        return _probe_report()

    monkeypatch.setattr(embedding_rollout, "probe", hanging_probe)
    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=0.01,
        poll_seconds=1,
    )

    assert report["status"] == "failed"
    assert report["error_code"] == "embedding_rollout_timed_out"


@pytest.mark.asyncio
async def test_embedding_rollout_times_out_after_slow_empty_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()

    async def fake_probe(settings: FoundationSettings) -> dict[str, object]:
        del settings
        return _probe_report()

    async def slow_empty_plan(*args: Any, **kwargs: Any) -> dict[str, object]:
        del args, kwargs
        clock.value = 2.0
        return _reindex_report(selected=0)

    monkeypatch.setattr(embedding_rollout, "probe", fake_probe)
    monkeypatch.setattr(embedding_rollout, "enqueue_reindex", slow_empty_plan)
    report = await embedding_rollout.run_embedding_rollout(
        FoundationSettings(),
        limit=1000,
        deadline_seconds=1,
        poll_seconds=1,
        clock=clock,
    )

    assert report["status"] == "failed"
    assert report["error_code"] == "embedding_reindex_timed_out"


def test_embedding_rollout_cli_redacts_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def invalid_settings() -> FoundationSettings:
        raise ValueError("EMBEDDING__API_KEY=must-not-leak")

    monkeypatch.setattr(embedding_rollout, "FoundationSettings", invalid_settings)
    monkeypatch.setattr(sys, "argv", ["enterprise-doc-embedding-rollout"])

    with pytest.raises(SystemExit, match="1"):
        embedding_rollout.main()

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["failed_stage"] == "configuration"
    assert report["error_code"] == "embedding_rollout_configuration_failed"
    assert "must-not-leak" not in str(report)


class _StaticProvider:
    def __init__(self, vectors: Sequence[Sequence[float]]) -> None:
        self.vectors = tuple(tuple(vector) for vector in vectors)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        del texts
        return self.vectors


@pytest.mark.asyncio
async def test_embedding_probe_marks_zero_vectors_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _StaticProvider(((0.0,) * 1024, (0.0,) * 1024))
    monkeypatch.setattr(
        embedding_probe,
        "build_embedding_provider",
        lambda settings: (provider, settings.model_name, settings.dimension),
    )

    report = await embedding_probe.probe(FoundationSettings())

    assert report["status"] == "failed"
    assert report["finite"] is True
    assert report["nonzero_norms"] is False
    assert report["values_redacted"] is True
