from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enterprise_doc_core.config import ObservabilitySettings
from enterprise_doc_core.telemetry import TelemetryManager, build_otlp_exporter


def test_disabled_telemetry_is_an_idempotent_noop() -> None:
    manager = TelemetryManager()
    settings = ObservabilitySettings(enabled=False)

    first = manager.initialize(settings=settings, service_name="test-service")
    second = manager.initialize(settings=settings, service_name="test-service")

    assert first is second
    assert first.enabled is False
    assert first.provider is None
    first.shutdown()


def test_in_memory_exporter_receives_safe_span() -> None:
    exporter = InMemorySpanExporter()
    manager = TelemetryManager()
    runtime = manager.initialize(
        settings=ObservabilitySettings(enabled=True),
        service_name="test-service",
        exporter=exporter,
    )

    with runtime.tracer.start_as_current_span("foundation.test") as span:
        span.set_attribute("app.component", "foundation")

    runtime.force_flush()
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["foundation.test"]
    assert spans[0].attributes["app.component"] == "foundation"
    runtime.shutdown()


def test_otlp_exporter_can_be_constructed_without_collector() -> None:
    exporter = build_otlp_exporter(
        ObservabilitySettings(
            enabled=True,
            exporter_otlp_endpoint="http://collector.example:4318",
        )
    )

    assert exporter is not None
