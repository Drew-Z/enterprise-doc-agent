"""Bounded resource observations; independent from task execution and readiness."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from enterprise_doc_core.telemetry.metrics import MetricsRuntime

ResourceReader = Callable[[], Awaitable[float]]


async def read_redis_connected_clients(client: Redis) -> float:
    info = await client.info("clients")
    count = info.get("connected_clients")
    if type(count) is not int or count < 0:
        raise ValueError("invalid_redis_connected_clients")
    return float(count)


class ResourceMetricsSampler:
    def __init__(
        self,
        metrics: MetricsRuntime,
        queue_reader: ResourceReader,
        redis_reader: ResourceReader,
        *,
        clock: Callable[[], float] = time.time,
        interval_seconds: float = 10,
        timeout_seconds: float = 2,
    ) -> None:
        if any(not math.isfinite(v) or v <= 0 for v in (interval_seconds, timeout_seconds)):
            raise ValueError("invalid_resource_sampling_budget")
        self.metrics = metrics
        self.readers = {"queue": queue_reader, "redis": redis_reader}
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds

    async def _sample(self, source: str, reader: ResourceReader) -> None:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                value = await reader()
        except asyncio.CancelledError:
            self.metrics.record_resource_observation(source, None, observed_at=self.clock())
            raise
        except Exception:
            # No endpoint, SQL, response body or upstream exception goes into metrics/logs.
            self.metrics.record_resource_observation(source, None, observed_at=self.clock())
        else:
            self.metrics.record_resource_observation(source, value, observed_at=self.clock())

    async def sample_once(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            for source, reader in self.readers.items():
                tasks.create_task(self._sample(source, reader))

    async def run(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            await self.sample_once()
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
