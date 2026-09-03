from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

_LOGGER = logging.getLogger("enterprise_doc_core.health")


class ComponentStatus(StrEnum):
    UP = "up"
    DOWN = "down"
    TIMEOUT = "timeout"


class OverallStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class ComponentHealth(BaseModel):
    status: ComponentStatus


class ReadinessResponse(BaseModel):
    status: OverallStatus
    checks: dict[str, ComponentHealth]
    checked_at: datetime


class HealthChecker(Protocol):
    name: str

    async def check(self) -> ComponentStatus: ...


class StaticChecker:
    def __init__(self, name: str, status: ComponentStatus) -> None:
        self.name = name
        self.status = status

    async def check(self) -> ComponentStatus:
        return self.status


class ReadinessCache:
    """Bound readiness probes while preserving a short stale-while-refresh window."""

    def __init__(
        self,
        checkers: Sequence[HealthChecker],
        *,
        timeout_seconds: float,
        ttl_seconds: float,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("readiness cache TTL must not be negative")
        self._checkers = tuple(checkers)
        self._timeout_seconds = timeout_seconds
        self._ttl_seconds = ttl_seconds
        self._refresh_lock = asyncio.Lock()
        self._cached: tuple[float, ReadinessResponse] | None = None

    async def get(self) -> ReadinessResponse:
        if self._ttl_seconds == 0:
            return await evaluate_readiness(
                self._checkers,
                timeout_seconds=self._timeout_seconds,
            )

        loop = asyncio.get_running_loop()
        cached = self._cached
        if cached is not None and loop.time() - cached[0] < self._ttl_seconds:
            return cached[1]

        # Once a value exists, do not make a burst of callers queue behind the
        # dependency checks. One caller refreshes; concurrent callers receive the
        # last bounded result. A failed refresh is still cached as not_ready.
        if cached is not None and self._refresh_lock.locked():
            return cached[1]

        async with self._refresh_lock:
            cached = self._cached
            if cached is not None and loop.time() - cached[0] < self._ttl_seconds:
                return cached[1]
            result = await evaluate_readiness(
                self._checkers,
                timeout_seconds=self._timeout_seconds,
            )
            self._cached = (loop.time(), result)
            return result


async def _run_checker(
    checker: HealthChecker,
    timeout_seconds: float,
) -> tuple[str, ComponentHealth]:
    try:
        status = await asyncio.wait_for(checker.check(), timeout=timeout_seconds)
    except TimeoutError:
        _LOGGER.warning(
            "health_check_timed_out",
            extra={"event_data": {"component": checker.name}},
        )
        status = ComponentStatus.TIMEOUT
    except Exception as exc:
        _LOGGER.warning(
            "health_check_failed",
            extra={
                "event_data": {
                    "component": checker.name,
                    "error_type": type(exc).__name__,
                }
            },
        )
        status = ComponentStatus.DOWN
    return checker.name, ComponentHealth(status=status)


async def evaluate_readiness(
    checkers: Sequence[HealthChecker],
    *,
    timeout_seconds: float,
) -> ReadinessResponse:
    results = await asyncio.gather(
        *(_run_checker(checker, timeout_seconds) for checker in checkers)
    )
    checks = dict(results)
    status = (
        OverallStatus.READY
        if checks and all(item.status is ComponentStatus.UP for item in checks.values())
        else OverallStatus.NOT_READY
    )
    return ReadinessResponse(
        status=status,
        checks=checks,
        checked_at=datetime.now(UTC),
    )
