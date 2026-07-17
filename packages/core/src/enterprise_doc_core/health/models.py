from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
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


class HealthChecker(Protocol):
    name: str

    async def check(self) -> ComponentStatus: ...


class StaticChecker:
    def __init__(self, name: str, status: ComponentStatus) -> None:
        self.name = name
        self.status = status

    async def check(self) -> ComponentStatus:
        return self.status


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
    return ReadinessResponse(status=status, checks=checks)
