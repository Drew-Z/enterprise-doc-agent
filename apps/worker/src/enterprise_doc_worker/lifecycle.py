from __future__ import annotations

import asyncio
import logging

from opentelemetry import trace
from opentelemetry.trace import Tracer

_LOGGER = logging.getLogger("enterprise_doc_worker.lifecycle")


class WorkerRuntime:
    def __init__(self, *, tracer: Tracer | None = None) -> None:
        self._shutdown = asyncio.Event()
        self._tracer = tracer or trace.get_tracer("enterprise-doc-worker")
        self.is_running = False

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self) -> None:
        with self._tracer.start_as_current_span("worker.lifecycle"):
            self.is_running = True
            _LOGGER.info("worker_started")
            try:
                await self._shutdown.wait()
            finally:
                self.is_running = False
                _LOGGER.info("worker_stopped")
