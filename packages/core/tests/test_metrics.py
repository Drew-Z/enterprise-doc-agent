from __future__ import annotations

from enterprise_doc_core.telemetry import MetricsRuntime, bounded_route, status_class


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
