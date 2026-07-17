from __future__ import annotations

import asyncio

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enterprise_doc_core.config import ObservabilitySettings
from enterprise_doc_core.telemetry import TelemetryManager
from enterprise_doc_worker.lifecycle import WorkerRuntime


async def test_worker_lifecycle_emits_span() -> None:
    exporter = InMemorySpanExporter()
    telemetry = TelemetryManager().initialize(
        settings=ObservabilitySettings(enabled=True),
        service_name="worker-test",
        exporter=exporter,
    )
    runtime = WorkerRuntime(tracer=telemetry.tracer)
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)

    runtime.request_shutdown()
    await task
    telemetry.force_flush()

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["worker.lifecycle"]
    telemetry.shutdown()
