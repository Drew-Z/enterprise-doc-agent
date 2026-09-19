from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
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


async def test_browser_callback_secrets_are_absent_from_exported_traces_and_logs(caplog) -> None:
    exporter = InMemorySpanExporter()
    runtime = TelemetryManager().initialize(
        settings=ObservabilitySettings(enabled=True),
        service_name="browser-trace-test",
        exporter=exporter,
    )
    app = create_app(settings=ApiSettings(_env_file=None), checkers=[], telemetry=runtime)
    markers = ("private-code-marker", "private-state-marker", "private-cookie-marker")
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="https://app.example.test"
            ) as client:
                response = await client.get(
                    f"/auth/callback?code={markers[0]}&state={markers[1]}",
                    headers={"Cookie": f"__Host-docagent-login={markers[2]}"},
                )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        runtime.force_flush()
        spans = exporter.get_finished_spans()
        assert any(span.name.startswith("GET /auth/callback") for span in spans)
        serialized = str([(span.name, dict(span.attributes), span.events) for span in spans])
        assert all(marker not in serialized and marker not in caplog.text for marker in markers)
        server = next(span for span in spans if span.name.startswith("GET /auth/callback"))
        for attribute in ("http.url", "url.full", "url.query", "http.target"):
            assert "?" not in str(server.attributes.get(attribute, ""))
    finally:
        runtime.shutdown()
