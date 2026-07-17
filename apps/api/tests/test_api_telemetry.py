from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enterprise_doc_api.app import create_app
from enterprise_doc_core.config import ObservabilitySettings
from enterprise_doc_core.telemetry import TelemetryManager


async def test_api_request_emits_span_without_sensitive_attributes() -> None:
    exporter = InMemorySpanExporter()
    runtime = TelemetryManager().initialize(
        settings=ObservabilitySettings(enabled=True),
        service_name="api-test",
        exporter=exporter,
    )
    app = create_app(telemetry=runtime)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live?token=must-not-appear")

    assert response.status_code == 200
    runtime.force_flush()
    spans = exporter.get_finished_spans()
    assert any(span.name.startswith("GET /health/live") for span in spans)
    serialized_attributes = str([dict(span.attributes) for span in spans])
    assert "must-not-appear" not in serialized_attributes
    assert "authorization" not in serialized_attributes.lower()
    runtime.shutdown()
