from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from enterprise_doc_core.jobs import ClaimedOutboxEvent
from enterprise_doc_worker.publisher import OutboxPublisher
from enterprise_doc_worker.queue import JobMessage


@dataclass
class FakeStore:
    claimed: tuple[ClaimedOutboxEvent, ...]
    marked: list[ClaimedOutboxEvent] = field(default_factory=list)
    claim_calls: int = 0

    async def claim(self, **_: object) -> tuple[ClaimedOutboxEvent, ...]:
        self.claim_calls += 1
        rows, self.claimed = self.claimed, ()
        return rows

    async def mark_published(self, event: ClaimedOutboxEvent) -> None:
        self.marked.append(event)


@dataclass
class FakeDispatcher:
    messages: list[tuple[JobMessage, object]] = field(default_factory=list)

    async def publish(self, message: JobMessage, *, task_id) -> None:
        self.messages.append((message, task_id))


def _event() -> ClaimedOutboxEvent:
    return ClaimedOutboxEvent(
        event_id=uuid4(),
        aggregate_id=uuid4(),
        tenant_id=uuid4(),
        event_type="document.ingest.requested",
        payload={"ignored": "payload is not trusted for routing"},
        lease_token=uuid4(),
        publisher_id="publisher-a",
    )


async def test_publisher_marks_only_successfully_dispatched_events() -> None:
    event = _event()
    store = FakeStore((event,))
    dispatcher = FakeDispatcher()
    publisher = OutboxPublisher(
        store=store,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        publisher_id="publisher-a",
    )

    assert await publisher.publish_once() == 1
    assert store.marked == [event]
    assert dispatcher.messages[0][0] == JobMessage(
        job_id=event.aggregate_id,
        tenant_id=event.tenant_id,
        event_id=event.event_id,
    )
    assert dispatcher.messages[0][1] == event.event_id


async def test_publisher_loop_stops_without_another_claim() -> None:
    store = FakeStore(())
    publisher = OutboxPublisher(
        store=store,  # type: ignore[arg-type]
        dispatcher=FakeDispatcher(),
        publisher_id="publisher-a",
        poll_interval_seconds=0.01,
    )
    stop = asyncio.Event()
    stop.set()

    await publisher.run(stop)

    assert store.claim_calls == 0


class FailingStore:
    def __init__(self) -> None:
        self.calls = 0

    async def claim(self, **_: object):
        self.calls += 1
        raise ConnectionError("temporary database outage")

    async def mark_published(self, _: ClaimedOutboxEvent) -> None:
        raise AssertionError("mark must not be called")


async def test_publisher_loop_survives_temporary_store_failure() -> None:
    store = FailingStore()
    publisher = OutboxPublisher(
        store=store,  # type: ignore[arg-type]
        dispatcher=FakeDispatcher(),
        publisher_id="publisher-a",
        poll_interval_seconds=0.01,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(publisher.run(stop))
    await asyncio.sleep(0.03)
    stop.set()
    await asyncio.wait_for(task, timeout=0.5)

    assert store.calls >= 1
