from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from enterprise_doc_core.agents import (
    CheckpointerCommand,
    CheckpointerReadiness,
    CheckpointHealthChecker,
    CheckpointRuntime,
    CheckpointSchemaNotReady,
)
from enterprise_doc_core.health import ComponentStatus


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(checkpoint_timeout_seconds=1.0, checkpoint_url=None),
        database=SimpleNamespace(url=SimpleNamespace(get_secret_value=lambda: "postgresql://test")),
    )


@pytest.mark.asyncio
async def test_checkpoint_runtime_keeps_saver_open_until_context_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enterprise_doc_core.agents.checkpoint as module

    readiness = CheckpointerReadiness(
        command=CheckpointerCommand.CHECK,
        ready=True,
        migration_version=7,
        expected_migration_version=7,
    )
    entered = False
    exited = False

    class Manager:
        async def __aenter__(self) -> object:
            nonlocal entered
            entered = True
            return object()

        async def __aexit__(self, *_: object) -> bool:
            nonlocal exited
            exited = True
            return False

    monkeypatch.setattr(module, "check_checkpoint_schema", lambda _: _ready(readiness))
    monkeypatch.setattr(
        module.AsyncPostgresSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: Manager(),
    )

    runtime = CheckpointRuntime(_settings())  # type: ignore[arg-type]
    async with runtime as saver:
        assert saver is runtime.saver
        assert entered and not exited
    assert exited
    with pytest.raises(RuntimeError):
        _ = runtime.saver


@pytest.mark.asyncio
async def test_checkpoint_runtime_refuses_to_open_when_schema_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enterprise_doc_core.agents.checkpoint as module

    readiness = CheckpointerReadiness(
        command=CheckpointerCommand.CHECK,
        ready=False,
        migration_version=None,
        expected_migration_version=7,
        missing_tables=("checkpoints",),
    )
    called = False

    def manager_factory(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(module, "check_checkpoint_schema", lambda _: _ready(readiness))
    monkeypatch.setattr(module.AsyncPostgresSaver, "from_conn_string", manager_factory)

    with pytest.raises(CheckpointSchemaNotReady):
        await CheckpointRuntime(_settings()).open()  # type: ignore[arg-type]
    assert not called


@pytest.mark.asyncio
async def test_checkpoint_runtime_preserves_timeout_when_manager_entry_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import enterprise_doc_core.agents.checkpoint as module

    readiness = CheckpointerReadiness(
        command=CheckpointerCommand.CHECK,
        ready=True,
        migration_version=7,
        expected_migration_version=7,
    )
    exited = False

    class Manager:
        async def __aenter__(self) -> object:
            await asyncio.sleep(60)
            return object()

        async def __aexit__(self, *_: object) -> bool:
            nonlocal exited
            exited = True
            return False

    settings = _settings()
    settings.agent.checkpoint_timeout_seconds = 0.01
    monkeypatch.setattr(module, "check_checkpoint_schema", lambda _: _ready(readiness))
    monkeypatch.setattr(
        module.AsyncPostgresSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: Manager(),
    )

    with pytest.raises(TimeoutError):
        await CheckpointRuntime(settings).open()  # type: ignore[arg-type]
    assert not exited


@pytest.mark.asyncio
async def test_checkpoint_health_checker_maps_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    import enterprise_doc_core.agents.checkpoint as module

    ready = CheckpointerReadiness(
        command=CheckpointerCommand.CHECK,
        ready=True,
        migration_version=7,
        expected_migration_version=7,
    )
    monkeypatch.setattr(module, "check_checkpoint_schema", lambda _: _ready(ready))
    assert await CheckpointHealthChecker(_settings()).check() is ComponentStatus.UP  # type: ignore[arg-type]


async def _ready(value: CheckpointerReadiness) -> CheckpointerReadiness:
    return value
