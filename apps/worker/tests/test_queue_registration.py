from __future__ import annotations

import asyncio
from uuid import uuid4

from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.queue import (
    JOB_TASK_NAME,
    AsyncTaskRunner,
    JobMessage,
    create_celery_app,
    register_job_task,
)


def test_new_consumer_app_does_not_inherit_a_closed_apps_runner():
    calls = []

    class Consumer:
        def __init__(self, label):
            self.label = label

        async def handle(self, message):
            calls.append((self.label, message.job_id))
            return "succeeded"

    first = create_celery_app(WorkerSettings(_env_file=None))
    old_runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    try:
        register_job_task(first, consumer_factory=lambda: Consumer("old"), async_runner=old_runner)
        first.finalize()
    finally:
        old_runner.close()
        first.close()

    second = create_celery_app(WorkerSettings(_env_file=None))
    new_runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    try:
        # An application without explicit registration must not receive the old
        # closure through Celery's process-wide shared-task finalization hooks.
        assert JOB_TASK_NAME not in second.tasks
        register_job_task(second, consumer_factory=lambda: Consumer("new"), async_runner=new_runner)
        message = JobMessage(job_id=uuid4(), tenant_id=uuid4(), event_id=uuid4())
        result = second.tasks[JOB_TASK_NAME].apply(
            args=[message.model_dump(mode="json")], throw=True
        )
        assert result.result == "succeeded"
        assert calls == [("new", message.job_id)]
    finally:
        new_runner.close()
        second.close()
