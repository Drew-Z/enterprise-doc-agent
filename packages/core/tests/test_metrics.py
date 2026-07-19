from __future__ import annotations

import pytest

from enterprise_doc_core.telemetry import (
    InstrumentedHealthChecker,
    InstrumentedModelGateway,
    MetricsRuntime,
    bounded_route,
    status_class,
)


def test_metrics_runtime_uses_an_independent_registry_and_bounded_labels() -> None:
    first = MetricsRuntime.create()
    second = MetricsRuntime.create()

    first.observe_api(
        method="GET",
        route="/api/jobs/{job_id}",
        status_code=200,
        duration=0.01,
    )
    first.observe_api(
        method="TRACE",
        route="/api/jobs/secret-identifier",
        status_code=599,
        duration=0.02,
    )
    first.observe_job(job_type="unknown.job", outcome="unknown", duration=0.1)

    rendered = first.render().decode("utf-8")
    assert "enterprise_doc_api_requests_total" in rendered
    assert "/api/jobs/secret-identifier" not in rendered
    assert "secret-identifier" not in rendered
    assert 'method="OTHER"' in rendered
    assert 'route="other"' in rendered or 'job_type="other"' in rendered
    assert first.registry is not second.registry
    assert 'route="/api/jobs/{job_id}"' not in second.render().decode("utf-8")


def test_route_and_status_helpers_are_deterministic() -> None:
    assert bounded_route("/api/jobs/{job_id}") == "/api/jobs/{job_id}"
    assert bounded_route("/api/jobs/" + "x" * 300) == "unmatched"
    assert bounded_route(None) == "unmatched"
    assert status_class(200) == "2xx"
    assert status_class(404) == "4xx"
    assert status_class(503) == "5xx"


def test_dependency_and_capacity_metrics_use_bounded_labels() -> None:
    metrics = MetricsRuntime.create()
    metrics.observe_dependency(dependency="object_store", result="success", duration=0.02)
    metrics.observe_dependency(dependency="tenant-secret", result="unknown", duration=-1)
    metrics.set_database_pool_utilization(72.5)
    metrics.set_queue_oldest_age(3.2)
    metrics.set_redis_connections(8)

    rendered = metrics.render().decode("utf-8")

    assert 'dependency="object_store",result="success"' in rendered
    assert "tenant-secret" not in rendered
    assert 'dependency="model",result="error"' in rendered
    assert "enterprise_doc_database_pool_utilization_percent 72.5" in rendered
    assert "enterprise_doc_queue_oldest_age_seconds 3.2" in rendered
    assert "enterprise_doc_redis_connections 8.0" in rendered


async def test_health_and_model_wrappers_record_success_and_failure() -> None:
    metrics = MetricsRuntime.create()

    class Checker:
        name = "redis"

        async def check(self) -> str:
            return "up"

    class Gateway:
        async def generate(self, request: object) -> str:
            if request == "fail":
                raise RuntimeError("model failed")
            return "ok"

    assert await InstrumentedHealthChecker(Checker(), metrics).check() == "up"
    gateway = InstrumentedModelGateway(Gateway(), metrics)
    assert await gateway.generate("ok") == "ok"
    with pytest.raises(RuntimeError, match="model failed"):
        await gateway.generate("fail")

    rendered = metrics.render().decode("utf-8")
    assert 'dependency="redis",result="success"' in rendered
    assert 'dependency="model",result="success"' in rendered
    assert 'dependency="model",result="error"' in rendered
