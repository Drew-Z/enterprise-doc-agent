from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Final, Protocol

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    exposition,
    generate_latest,
)

_ROUTE_FALLBACK: Final = "unmatched"
_STATIC_ROUTE_SEGMENTS: Final = frozenset(
    {
        "api",
        "health",
        "live",
        "ready",
        "metrics",
        "jobs",
        "agent-runs",
        "events",
        "approvals",
        "artifacts",
        "uploads",
        "upload-sessions",
        "documents",
        "versions",
        "parts",
        "complete",
        "cancel",
        "download",
        "decide",
        "ready-document-versions",
    }
)
_STATUS_CLASSES: Final = frozenset({"2xx", "3xx", "4xx", "5xx"})
_JOB_OUTCOMES: Final = frozenset({"succeeded", "failed", "cancelled", "duplicate_or_not_claimable"})
_PUBLISH_RESULTS: Final = frozenset({"success", "error"})
_DEPENDENCIES: Final = frozenset({"database", "redis", "object_store", "model"})
_DEPENDENCY_RESULTS: Final = frozenset({"success", "error"})


class _HealthChecker(Protocol):
    name: str

    async def check(self) -> Any: ...


def status_class(status_code: int) -> str:
    value = f"{status_code // 100}xx"
    return value if value in _STATUS_CLASSES else "5xx"


def bounded_route(route: str | None) -> str:
    """Keep route labels stable and reject raw paths containing identifiers."""
    if route is None or not route.startswith("/") or len(route) > 160:
        return _ROUTE_FALLBACK
    segments = tuple(segment for segment in route.split("/") if segment)
    if any(
        segment not in _STATIC_ROUTE_SEGMENTS
        and not (segment.startswith("{") and segment.endswith("}"))
        for segment in segments
    ):
        return _ROUTE_FALLBACK
    return route


def bounded_job_type(job_type: str) -> str:
    if job_type in {"document.ingest", "agent.execute"}:
        return job_type
    return "other"


@dataclass(slots=True)
class MetricsRuntime:
    registry: CollectorRegistry
    api_requests_total: Counter
    api_request_duration_seconds: Histogram
    worker_job_claims_total: Counter
    worker_jobs_completed_total: Counter
    worker_job_duration_seconds: Histogram
    worker_heartbeats_total: Counter
    outbox_publish_total: Counter
    outbox_publish_duration_seconds: Histogram
    dependency_duration_seconds: Histogram
    dependency_up: Gauge
    database_pool_utilization_percent: Gauge
    queue_oldest_age_seconds: Gauge
    redis_connections: Gauge

    @classmethod
    def create(cls) -> MetricsRuntime:
        registry = CollectorRegistry(auto_describe=True)
        return cls(
            registry=registry,
            api_requests_total=Counter(
                "enterprise_doc_api_requests_total",
                "HTTP requests handled by the API.",
                ("method", "route", "status_class"),
                registry=registry,
            ),
            api_request_duration_seconds=Histogram(
                "enterprise_doc_api_request_duration_seconds",
                "API request duration in seconds.",
                ("method", "route", "status_class"),
                # Keep buckets useful for control-plane latency without implying an SLO.
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
                registry=registry,
            ),
            worker_job_claims_total=Counter(
                "enterprise_doc_worker_job_claims_total",
                "Job claim dispositions observed by a worker.",
                ("job_type", "result"),
                registry=registry,
            ),
            worker_jobs_completed_total=Counter(
                "enterprise_doc_worker_jobs_completed_total",
                "Terminal job outcomes observed by a worker.",
                ("job_type", "outcome"),
                registry=registry,
            ),
            worker_job_duration_seconds=Histogram(
                "enterprise_doc_worker_job_duration_seconds",
                "Job handling duration in seconds.",
                ("job_type", "outcome"),
                buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 300, 900),
                registry=registry,
            ),
            worker_heartbeats_total=Counter(
                "enterprise_doc_worker_heartbeats_total",
                "Job heartbeat outcomes.",
                ("result",),
                registry=registry,
            ),
            outbox_publish_total=Counter(
                "enterprise_doc_worker_outbox_publish_total",
                "Outbox publication outcomes.",
                ("result",),
                registry=registry,
            ),
            outbox_publish_duration_seconds=Histogram(
                "enterprise_doc_worker_outbox_publish_duration_seconds",
                "Outbox publication duration in seconds.",
                ("result",),
                buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
                registry=registry,
            ),
            dependency_duration_seconds=Histogram(
                "enterprise_doc_dependency_duration_seconds",
                "Dependency call duration by bounded dependency name.",
                ("dependency", "result"),
                buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15, 60),
                registry=registry,
            ),
            dependency_up=Gauge(
                "enterprise_doc_dependency_up",
                "Whether the latest dependency health check succeeded.",
                ("dependency",),
                registry=registry,
            ),
            database_pool_utilization_percent=Gauge(
                "enterprise_doc_database_pool_utilization_percent",
                "Database connection pool utilization percentage.",
                registry=registry,
            ),
            queue_oldest_age_seconds=Gauge(
                "enterprise_doc_queue_oldest_age_seconds",
                "Age in seconds of the oldest observed queued job.",
                registry=registry,
            ),
            redis_connections=Gauge(
                "enterprise_doc_redis_connections",
                "Observed Redis connections used by the process.",
                registry=registry,
            ),
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)

    @property
    def content_type(self) -> str:
        return exposition.CONTENT_TYPE_LATEST

    def observe_api(
        self,
        *,
        method: str,
        route: str | None,
        status_code: int,
        duration: float,
    ) -> None:
        safe_method = (
            method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
        )
        labels = (safe_method, bounded_route(route), status_class(status_code))
        self.api_requests_total.labels(*labels).inc()
        self.api_request_duration_seconds.labels(*labels).observe(max(duration, 0.0))

    def observe_job_claim(self, *, job_type: str, result: str) -> None:
        safe_result = result if result in {"claimed", "duplicate_or_not_claimable"} else "error"
        self.worker_job_claims_total.labels(bounded_job_type(job_type), safe_result).inc()

    def observe_job(self, *, job_type: str, outcome: str, duration: float) -> None:
        safe_outcome = outcome if outcome in _JOB_OUTCOMES else "failed"
        labels = (bounded_job_type(job_type), safe_outcome)
        self.worker_jobs_completed_total.labels(*labels).inc()
        self.worker_job_duration_seconds.labels(*labels).observe(max(duration, 0.0))

    def observe_heartbeat(self, result: str) -> None:
        safe_result = (
            result if result in {"ok", "cancel_requested", "lease_lost", "error"} else "error"
        )
        self.worker_heartbeats_total.labels(safe_result).inc()

    def observe_publish(self, *, result: str, duration: float) -> None:
        safe_result = result if result in _PUBLISH_RESULTS else "error"
        self.outbox_publish_total.labels(safe_result).inc()
        self.outbox_publish_duration_seconds.labels(safe_result).observe(max(duration, 0.0))

    def observe_dependency(self, *, dependency: str, result: str, duration: float) -> None:
        safe_dependency = dependency if dependency in _DEPENDENCIES else "model"
        safe_result = result if result in _DEPENDENCY_RESULTS else "error"
        self.dependency_duration_seconds.labels(safe_dependency, safe_result).observe(
            max(duration, 0.0)
        )
        self.dependency_up.labels(safe_dependency).set(1 if safe_result == "success" else 0)

    def set_database_pool_utilization(self, utilization_percent: float) -> None:
        self.database_pool_utilization_percent.set(max(0.0, utilization_percent))

    def set_queue_oldest_age(self, age_seconds: float) -> None:
        self.queue_oldest_age_seconds.set(max(0.0, age_seconds))

    def set_redis_connections(self, connections: float) -> None:
        self.redis_connections.set(max(0.0, connections))


class InstrumentedHealthChecker:
    """Observe bounded dependency health checks without exposing checker internals."""

    def __init__(self, checker: _HealthChecker, metrics: MetricsRuntime) -> None:
        self._checker = checker
        self._metrics = metrics
        self.name = str(getattr(checker, "name", "other"))

    async def check(self) -> Any:
        started = perf_counter()
        try:
            result = await self._checker.check()
        except Exception:
            self._metrics.observe_dependency(
                dependency=self.name,
                result="error",
                duration=perf_counter() - started,
            )
            raise
        self._metrics.observe_dependency(
            dependency=self.name,
            result="success" if str(result) == "up" else "error",
            duration=perf_counter() - started,
        )
        return result


def instrument_health_checkers(
    checkers: Sequence[_HealthChecker], metrics: MetricsRuntime
) -> tuple[_HealthChecker, ...]:
    return tuple(InstrumentedHealthChecker(checker, metrics) for checker in checkers)


class InstrumentedModelGateway:
    """Measure model gateway duration while preserving its protocol and errors."""

    def __init__(self, gateway: object, metrics: MetricsRuntime) -> None:
        self._gateway = gateway
        self._metrics = metrics

    async def generate(self, request: object) -> object:
        started = perf_counter()
        try:
            result = await self._gateway.generate(request)  # type: ignore[attr-defined]
        except Exception:
            self._metrics.observe_dependency(
                dependency="model",
                result="error",
                duration=perf_counter() - started,
            )
            raise
        self._metrics.observe_dependency(
            dependency="model",
            result="success",
            duration=perf_counter() - started,
        )
        return result

    async def healthcheck(self) -> object:
        check = getattr(self._gateway, "healthcheck", None)
        if not callable(check):
            return False
        return await check()
