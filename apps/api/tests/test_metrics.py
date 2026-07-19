from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app


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
