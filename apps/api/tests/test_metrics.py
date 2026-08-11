from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import AgentSettings, EmbeddingSettings
from enterprise_doc_core.telemetry import MetricsRuntime


async def test_api_metrics_endpoint_is_prometheus_compatible_and_excludes_itself() -> None:
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        metrics = await client.get("/metrics")

    assert live.status_code == 200
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    body = metrics.text
    assert "enterprise_doc_api_requests_total" in body
    assert "/health/live" in body
    assert 'route="/metrics"' not in body
    assert "x-request-id" not in body


def test_api_default_services_share_the_process_metrics_registry() -> None:
    metrics = MetricsRuntime.create()
    app = create_app(metrics=metrics)

    assert app.state.metrics is metrics
    assert app.state.approval_service.metrics is metrics
    assert app.state.agent_artifact_service.metrics is metrics
    assert app.state.agent_artifact_service.artifact_store.metrics is metrics
    assert app.state.upload_creation_service.object_store.metrics is metrics


def test_api_default_agent_services_share_the_execution_retry_budget() -> None:
    settings = ApiSettings(agent=AgentSettings(execution_max_attempts=5))
    app = create_app(settings=settings)

    assert app.state.agent_run_service.agent_settings.execution_max_attempts == 5
    assert app.state.approval_service.resume_max_attempts == 5


def test_api_default_upload_service_uses_embedding_ingestion_retry_budget() -> None:
    settings = ApiSettings(embedding=EmbeddingSettings(ingestion_max_attempts=5))
    app = create_app(settings=settings)

    assert app.state.upload_session_service.ingestion_max_attempts == 5
