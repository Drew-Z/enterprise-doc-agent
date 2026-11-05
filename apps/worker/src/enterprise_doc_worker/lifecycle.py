from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from pathlib import PurePath

from opentelemetry import trace
from opentelemetry.trace import Tracer

from enterprise_doc_core.health import ComponentStatus

_LOGGER = logging.getLogger("enterprise_doc_worker.lifecycle")


def _waiting_at(role: str) -> list[str]:
    """Only code locations from owned tasks; never frame locals or exception text."""
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:
        return []
    for task in tasks:
        if task.get_name() != f"worker.{role}":
            continue
        waiting: list[str] = []
        current: object = task.get_coro()
        for _ in range(20):
            frame = getattr(current, "cr_frame", getattr(current, "gi_frame", None))
            if frame is not None:
                code = frame.f_code
                waiting.append(f"{PurePath(code.co_filename).name}:{code.co_name}:{frame.f_lineno}")
            current = getattr(current, "cr_await", getattr(current, "gi_yieldfrom", None))
            if current is None:
                break
        return waiting
    return []


class WorkerProgress:
    """Process-local progress check, independent of dependency I/O and its cancellation."""

    name = "worker_progress"

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._deadlines: dict[str, float] = {}
        self._failed = False

    def register(self, role: str, *, timeout_seconds: float) -> Callable[[], None]:
        if role in self._deadlines or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("invalid_worker_progress_budget")
        self._deadlines[role] = self.clock() + timeout_seconds

        def completed_cycle() -> None:
            if self.is_healthy():
                self._deadlines[role] = self.clock() + timeout_seconds

        return completed_cycle

    def stop(self) -> None:
        self._failed = True

    def is_healthy(self) -> bool:
        if not self._failed:
            for role, deadline in self._deadlines.items():
                if self.clock() >= deadline:
                    self._failed = True
                    _LOGGER.error(
                        "worker_progress_stalled",
                        extra={"event_data": {"role": role, "waiting_at": _waiting_at(role)}},
                    )
                    break
        return not self._failed

    async def check(self) -> ComponentStatus:
        return ComponentStatus.UP if self.is_healthy() else ComponentStatus.DOWN


class WorkerRuntime:
    def __init__(self, *, tracer: Tracer | None = None) -> None:
        self._shutdown = asyncio.Event()
        self._tracer = tracer or trace.get_tracer("enterprise-doc-worker")
        self.is_running = False
        self.accepting_claims = True

    def request_shutdown(self) -> None:
        self.accepting_claims = False
        self._shutdown.set()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown

    async def run(self) -> None:
        with self._tracer.start_as_current_span("worker.lifecycle"):
            self.is_running = True
            _LOGGER.info("worker_started")
            try:
                await self._shutdown.wait()
            finally:
                self.is_running = False
                _LOGGER.info("worker_stopped")
