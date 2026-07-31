from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Protocol
from uuid import UUID

from enterprise_doc_core.jobs import ClaimedOutboxEvent
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.queue import JobMessage, TaskDispatcher

_LOGGER = logging.getLogger("enterprise_doc_worker.publisher")


class OutboxStore(Protocol):
    async def claim(
        self,
        *,
        publisher_id: str,
        limit: int = 20,
        event_id: UUID | None = None,
    ) -> tuple[ClaimedOutboxEvent, ...]: ...

    async def mark_published(self, event: ClaimedOutboxEvent) -> None: ...


class OutboxPublisher:
    def __init__(
        self,
        *,
        store: OutboxStore,
        dispatcher: TaskDispatcher,
        publisher_id: str,
        batch_size: int = 20,
        poll_interval_seconds: float = 1.0,
        cycle_timeout_seconds: float = 30.0,
        metrics: MetricsRuntime | None = None,
    ) -> None:
        if batch_size <= 0 or poll_interval_seconds <= 0:
            raise ValueError("publisher batch size and poll interval must be positive")
        if cycle_timeout_seconds <= 0:
            raise ValueError("publisher cycle timeout must be positive")
        self.store = store
        self.dispatcher = dispatcher
        self.publisher_id = publisher_id
        self.batch_size = batch_size
        self.poll_interval_seconds = poll_interval_seconds
        self.cycle_timeout_seconds = cycle_timeout_seconds
        self.metrics = metrics

    async def publish_once(self) -> int:
        claimed = await self.store.claim(
            publisher_id=self.publisher_id,
            limit=self.batch_size,
        )
        published = 0
        for event in claimed:
            started = perf_counter()
            message = JobMessage(
                job_id=event.aggregate_id,
                tenant_id=event.tenant_id,
                event_id=event.event_id,
            )
            try:
                await self.dispatcher.publish(message, task_id=event.event_id)
                await self.store.mark_published(event)
                published += 1
                if self.metrics is not None:
                    self.metrics.observe_publish(
                        result="success",
                        duration=perf_counter() - started,
                    )
            except Exception as error:
                if self.metrics is not None:
                    self.metrics.observe_publish(
                        result="error",
                        duration=perf_counter() - started,
                    )
                _LOGGER.warning(
                    "outbox_publish_failed",
                    extra={"event_data": {"error_class": type(error).__name__}},
                )
        return published

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with asyncio.timeout(self.cycle_timeout_seconds):
                    await self.publish_once()
            except TimeoutError:
                _LOGGER.warning("outbox_poll_timeout")
            except Exception as error:
                _LOGGER.warning(
                    "outbox_poll_failed",
                    extra={"event_data": {"error_class": type(error).__name__}},
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue
