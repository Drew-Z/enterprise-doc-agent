from __future__ import annotations

import asyncio

from enterprise_doc_worker.lifecycle import WorkerRuntime


async def test_worker_runtime_waits_until_shutdown() -> None:
    runtime = WorkerRuntime()
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)

    assert task.done() is False
    assert runtime.is_running is True
    assert runtime.accepting_claims is True

    runtime.request_shutdown()
    assert runtime.accepting_claims is False
    await asyncio.wait_for(task, timeout=0.5)

    assert runtime.is_running is False


async def test_shutdown_is_idempotent() -> None:
    runtime = WorkerRuntime()
    runtime.request_shutdown()
    runtime.request_shutdown()

    await asyncio.wait_for(runtime.run(), timeout=0.5)

    assert runtime.is_running is False
