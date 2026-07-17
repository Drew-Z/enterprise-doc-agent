from __future__ import annotations

import asyncio
from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_core.health import ComponentStatus


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


async def request(app: object, path: str) -> tuple[int, dict[str, object]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:  # type: ignore[arg-type]
        response = await client.get(path)
    return response.status_code, response.json()


async def test_liveness_never_calls_dependency_checkers() -> None:
    checker = FakeChecker("database", result=ComponentStatus.DOWN)
    app = create_app(checkers=[checker])

    status, body = await request(app, "/health/live")

    assert status == 200
    assert body == {"status": "alive"}
    assert checker.calls == 0


async def test_readiness_returns_typed_success_response() -> None:
    app = create_app(
        checkers=[
            FakeChecker("database"),
            FakeChecker("redis"),
            FakeChecker("object_store"),
        ]
    )

    status, body = await request(app, "/health/ready")

    assert status == 200
    assert body == {
        "status": "ready",
        "checks": {
            "database": {"status": "up"},
            "redis": {"status": "up"},
            "object_store": {"status": "up"},
        },
    }


async def test_readiness_returns_typed_503_for_failure_and_timeout() -> None:
    app = create_app(
        checkers=[
            FakeChecker("database", result=ComponentStatus.DOWN),
            FakeChecker("redis", delay=0.1),
            FakeChecker("object_store"),
        ],
        readiness_timeout_seconds=0.01,
    )

    status, body = await request(app, "/health/ready")

    assert status == 503
    assert body["status"] == "not_ready"
    assert body["checks"] == {
        "database": {"status": "down"},
        "redis": {"status": "timeout"},
        "object_store": {"status": "up"},
    }
