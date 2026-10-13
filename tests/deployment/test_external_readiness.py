from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from scripts.check_external_readiness import probe_readiness


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({}, "ready"),
        ({"status": "not_ready"}, "dependency_unhealthy"),
        ({"checks": {}}, "invalid_readiness"),
        ({"checks": {"database": {"status": "up"}}}, "invalid_readiness"),
        ({"checked_at": "2026-09-01T00:00:00Z"}, "stale_readiness"),
        ({"checked_at": "2026-09-25T12:00:00"}, "invalid_readiness"),
    ],
)
async def test_probe_requires_fresh_complete_readiness(change: dict, reason: str) -> None:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    body = {
        "status": "ready",
        "checks": {name: {"status": "up"} for name in ("database", "redis", "object_store")},
        "checked_at": (now - timedelta(seconds=1)).isoformat(),
        **change,
    }
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    result = await probe_readiness(
        "https://service.example/health/ready",
        transport=httpx.MockTransport(respond),
        clock=lambda: now,
    )
    assert result["reason"] == reason
    assert result["healthy"] is (reason == "ready")
    assert result["notifications_sent"] == 0
    assert len(requests) == 1
    assert requests[0].headers["accept"] == "application/json"
    assert requests[0].headers["user-agent"] == "docagent-ops-readiness/1.0"
    assert "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            httpx.Response(302, headers={"Location": "https://identity.example/login"}),
            "http_status",
        ),
        (httpx.Response(503, json={"status": "not_ready"}), "http_status"),
        (httpx.Response(200, text="<html>sign in</html>"), "invalid_content_type"),
        (
            httpx.Response(200, content=b"not json", headers={"content-type": "application/json"}),
            "invalid_readiness",
        ),
        (
            httpx.Response(200, content=b"x" * 65537, headers={"content-type": "application/json"}),
            "response_too_large",
        ),
    ],
)
async def test_probe_rejects_false_success_and_does_not_follow_redirects(response, reason):
    requests = []

    def respond(request):
        requests.append(request)
        return response

    result = await probe_readiness(
        "https://service.example/health/ready", transport=httpx.MockTransport(respond)
    )
    assert result["reason"] == reason
    assert result["healthy"] is False
    assert len(requests) == 1
    assert "sign in" not in json.dumps(result)


async def test_probe_deadline_includes_slow_response_stream_and_redacts_transport_errors():
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield b"{}"

    result = await probe_readiness(
        "https://service.example/health/ready",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=SlowBody(), headers={"content-type": "application/json"}
            )
        ),
    )
    assert result["reason"] == "timeout"
    assert result["elapsed_ms"] < 1000

    def failed(request):
        raise httpx.ConnectError("secret-upstream-diagnostic", request=request)

    result = await probe_readiness(
        "https://service.example/health/ready", transport=httpx.MockTransport(failed)
    )
    assert result["reason"] == "transport_error"
    assert "secret-upstream-diagnostic" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://service.example/health/ready",
        "https://user:password@service.example/health/ready",
        "https://service.example/health/ready?token=secret",
        "https://service.example/",
    ],
)
async def test_invalid_probe_configuration_fails_before_network(url):
    def unexpected(request):
        pytest.fail("invalid configuration reached the network")

    with pytest.raises(ValueError, match="configuration"):
        await probe_readiness(url, transport=httpx.MockTransport(unexpected))
