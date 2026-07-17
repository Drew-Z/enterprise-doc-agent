from __future__ import annotations

import asyncio
from dataclasses import dataclass

from enterprise_doc_core.health import (
    ComponentStatus,
    OverallStatus,
    evaluate_readiness,
)


@dataclass
class FakeChecker:
    name: str
    result: ComponentStatus = ComponentStatus.UP
    delay: float = 0
    calls: int = 0

    async def check(self) -> ComponentStatus:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


async def test_readiness_is_ready_only_when_every_component_is_up() -> None:
    checkers = [
        FakeChecker("database"),
        FakeChecker("redis"),
        FakeChecker("object_store"),
    ]

    result = await evaluate_readiness(checkers, timeout_seconds=0.1)

    assert result.status is OverallStatus.READY
    assert {name: state.status for name, state in result.checks.items()} == {
        "database": ComponentStatus.UP,
        "redis": ComponentStatus.UP,
        "object_store": ComponentStatus.UP,
    }


async def test_readiness_maps_failures_and_timeouts_without_raising() -> None:
    checkers = [
        FakeChecker("database", result=ComponentStatus.DOWN),
        FakeChecker("redis", delay=0.1),
        FakeChecker("object_store"),
    ]

    result = await evaluate_readiness(checkers, timeout_seconds=0.01)

    assert result.status is OverallStatus.NOT_READY
    assert result.checks["database"].status is ComponentStatus.DOWN
    assert result.checks["redis"].status is ComponentStatus.TIMEOUT
    assert result.checks["object_store"].status is ComponentStatus.UP


async def test_readiness_runs_checkers_concurrently() -> None:
    checkers = [FakeChecker("database", delay=0.03), FakeChecker("redis", delay=0.03)]
    loop = asyncio.get_running_loop()
    started = loop.time()

    await evaluate_readiness(checkers, timeout_seconds=0.1)

    assert loop.time() - started < 0.055
