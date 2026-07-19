from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_core.context import get_request_context


def add_context_echo_route(app: FastAPI) -> None:
    @app.get("/test/context")
    async def context_echo() -> dict[str, str | None]:
        context = get_request_context()
        await asyncio.sleep(0.01)
        return {
            "request_id": context.request_id if context else None,
            "correlation_id": context.correlation_id if context else None,
        }


async def test_request_ids_are_propagated_and_returned() -> None:
    app = create_app()
    add_context_echo_route(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/test/context",
            headers={
                "X-Request-ID": "request-123",
                "X-Correlation-ID": "correlation-456",
            },
        )

    assert response.json() == {
        "request_id": "request-123",
        "correlation_id": "correlation-456",
    }
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.headers["X-Correlation-ID"] == "correlation-456"
    assert get_request_context() is None


async def test_missing_or_invalid_ids_are_replaced() -> None:
    app = create_app()
    add_context_echo_route(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/test/context",
            headers={"X-Request-ID": "invalid id with spaces", "X-Correlation-ID": "x" * 200},
        )

    payload = response.json()
    assert payload["request_id"] != "invalid id with spaces"
    assert payload["correlation_id"] != "x" * 200
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert payload["correlation_id"] == response.headers["X-Correlation-ID"]


async def test_concurrent_requests_keep_context_isolated() -> None:
    app = create_app()
    add_context_echo_route(app)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.get(
                "/test/context",
                headers={"X-Request-ID": "request-a", "X-Correlation-ID": "correlation-a"},
            ),
            client.get(
                "/test/context",
                headers={"X-Request-ID": "request-b", "X-Correlation-ID": "correlation-b"},
            ),
        )

    assert first.json() == {"request_id": "request-a", "correlation_id": "correlation-a"}
    assert second.json() == {"request_id": "request-b", "correlation_id": "correlation-b"}
    assert get_request_context() is None


async def test_request_logs_use_route_template_instead_of_resource_identifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/test/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    with caplog.at_level(logging.INFO, logger="enterprise_doc_api.request"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test/items/secret-resource-id")

    assert response.status_code == 200
    records = [record for record in caplog.records if record.msg == "request_completed"]
    assert records
    assert records[-1].event_data["route"] == "/test/items/{item_id}"
    assert "secret-resource-id" not in str(records[-1].event_data)
