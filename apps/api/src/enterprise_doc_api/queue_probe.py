from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from time import time

import httpx

from enterprise_doc_core.telemetry.queue_health import QueueObservation

WORKER_QUEUE_URL = "http://enterprise-doc-worker:8081/health/queue"


class QueueProbe:
    """Bounded background read; public requests only access the last safe projection."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time,
        timeout_seconds: float = 2,
        interval_seconds: float = 10,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 2
            or not math.isfinite(interval_seconds)
            or not 0 < interval_seconds <= 10
        ):
            raise ValueError("invalid_queue_probe_budget")
        self._clock = clock
        self._timeout = timeout_seconds
        self._interval = interval_seconds
        self._observation = QueueObservation()
        self._client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_seconds,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    @property
    def current(self) -> QueueObservation:
        return self._observation.at(self._clock())

    async def sample_once(self) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream(
                    "GET",
                    WORKER_QUEUE_URL,
                    headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "").split(";")[0].strip().lower()
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise ValueError("invalid_queue_response")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 1024:
                            raise ValueError("queue_response_too_large")
                        data.extend(chunk)
                    self._observation = QueueObservation.model_validate_json(bytes(data)).at(
                        self._clock()
                    )
        except asyncio.CancelledError:
            self._observation = QueueObservation()
            raise
        except Exception:
            # HTTP, parsing and timeout are one closed boundary. Never forward
            # error bodies or retain the preceding successful sample on failure.
            self._observation = QueueObservation()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                if not stop.is_set():
                    await self.sample_once()

    async def close(self) -> None:
        await self._client.aclose()
