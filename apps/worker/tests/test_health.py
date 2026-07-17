from __future__ import annotations

import asyncio
from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from enterprise_doc_core.health import ComponentStatus
from enterprise_doc_worker.app import create_probe_app


@dataclass
class FakeChecker:
    name: str
    result: ComponentStatus = ComponentStatus.UP
    delay: float = 0
    calls: int = 0

    async def check(self) -> ComponentStatus:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


async def test_worker_liveness_does_not_call_dependencies() -> None:
    checker = FakeChecker("database", result=ComponentStatus.DOWN)
    app = create_probe_app(checkers=[checker])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert checker.calls == 0


async def test_worker_readiness_matches_api_contract() -> None:
    app = create_probe_app(
        checkers=[
            FakeChecker("database"),
            FakeChecker("redis", result=ComponentStatus.DOWN),
            FakeChecker("object_store", delay=0.1),
        ],
        readiness_timeout_seconds=0.01,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": {"status": "up"},
            "redis": {"status": "down"},
            "object_store": {"status": "timeout"},
        },
    }
