from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Tracer

from enterprise_doc_core.config import ObservabilitySettings


def build_otlp_exporter(settings: ObservabilitySettings) -> OTLPSpanExporter:
    endpoint = settings.exporter_otlp_endpoint.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint = f"{endpoint}/v1/traces"
    return OTLPSpanExporter(endpoint=endpoint)


@dataclass(slots=True)
class TelemetryRuntime:
    enabled: bool
    tracer: Tracer
    provider: TracerProvider | None = None
    _closed: bool = False

    def force_flush(self) -> bool:
        if self.provider is None:
            return True
        return self.provider.force_flush()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.provider is not None:
            self.provider.shutdown()


class TelemetryManager:
    def __init__(self) -> None:
        self._runtime: TelemetryRuntime | None = None

    def initialize(
        self,
        *,
        settings: ObservabilitySettings,
        service_name: str,
        exporter: SpanExporter | None = None,
    ) -> TelemetryRuntime:
        if self._runtime is not None:
            return self._runtime

        if not settings.enabled:
            noop_provider = trace.NoOpTracerProvider()
            self._runtime = TelemetryRuntime(
                enabled=False,
                tracer=noop_provider.get_tracer(service_name),
            )
            return self._runtime

        sdk_provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(settings.sample_ratio)),
        )
        sdk_provider.add_span_processor(
            SimpleSpanProcessor(exporter or build_otlp_exporter(settings))
        )
        self._runtime = TelemetryRuntime(
            enabled=True,
            tracer=sdk_provider.get_tracer(service_name),
            provider=sdk_provider,
        )
        return self._runtime
