import asyncio

import uvicorn

from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import TelemetryManager
from enterprise_doc_worker.app import create_probe_app
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.lifecycle import WorkerRuntime


async def run_worker() -> None:
    settings = WorkerSettings()
    configure_logging(
        service="worker",
        environment=settings.app_env.value,
        level=settings.log_level,
    )
    telemetry = TelemetryManager().initialize(
        settings=settings.otel,
        service_name="enterprise-doc-worker",
    )
    runtime = WorkerRuntime(tracer=telemetry.tracer)
    server = uvicorn.Server(
        uvicorn.Config(
            create_probe_app(settings=settings),
            host=settings.worker.host,
            port=settings.worker.probe_port,
            loop="none",
            log_config=None,
        )
    )
    runtime_task = asyncio.create_task(runtime.run())
    try:
        await server.serve()
    finally:
        runtime.request_shutdown()
        await runtime_task
        telemetry.shutdown()


def main() -> None:
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run_worker())


if __name__ == "__main__":
    main()
