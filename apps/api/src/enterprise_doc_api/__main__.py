import uvicorn

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import TelemetryManager


def main() -> None:
    ensure_asyncio_compatibility()
    settings = ApiSettings()
    configure_logging(
        service="api",
        environment=settings.app_env.value,
        level=settings.log_level,
    )
    telemetry = TelemetryManager().initialize(
        settings=settings.otel,
        service_name="enterprise-doc-api",
    )
    uvicorn.run(
        create_app(settings=settings, telemetry=telemetry),
        host=settings.api.host,
        port=settings.api.port,
        loop="enterprise_doc_core.db.engine:selector_event_loop_factory",
        log_config=None,
    )
    telemetry.shutdown()


if __name__ == "__main__":
    main()
