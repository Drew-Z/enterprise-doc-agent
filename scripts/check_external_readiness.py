"""One bounded external probe; notification routing belongs to the chosen monitor."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx
from pydantic import ValidationError

from enterprise_doc_core.health.models import (
    ComponentStatus,
    OverallStatus,
    ReadinessResponse,
)

MAX_RESPONSE_BYTES = 64 * 1024
REQUIRED_CHECKS = frozenset({"database", "redis", "object_store"})


async def probe_readiness(
    url: str,
    *,
    timeout_seconds: float = 10,
    max_age_seconds: float = 120,
    proxy: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    """Read a real readiness body without credentials, redirects, or retries."""
    parsed = urlsplit(url)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/health/ready"
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            )
        )
        or any(
            not math.isfinite(value) or value <= 0 for value in (timeout_seconds, max_age_seconds)
        )
    ):
        raise ValueError("invalid probe configuration")
    started = time.monotonic()
    status_code: int | None = None
    reason = "invalid_readiness"
    checked_at: str | None = None
    try:
        async with (
            asyncio.timeout(timeout_seconds),
            httpx.AsyncClient(
                transport=transport,
                proxy=proxy,
                timeout=timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "User-Agent": "docagent-ops-readiness/1.0"},
            ) as client,
            client.stream("GET", url) as response,
        ):
            status_code = response.status_code
            if status_code != 200:
                reason = "http_status"
            elif (
                response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                reason = "invalid_content_type"
            else:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        reason = "response_too_large"
                        break
                else:
                    payload = ReadinessResponse.model_validate_json(bytes(content))
                    now = clock()
                    if (
                        payload.checked_at.utcoffset() is None
                        or now.utcoffset() is None
                        or not REQUIRED_CHECKS.issubset(payload.checks)
                    ):
                        reason = "invalid_readiness"
                    else:
                        age = (now - payload.checked_at).total_seconds()
                        checked_at = payload.checked_at.isoformat()
                        if age < -15:
                            reason = "clock_skew"
                        elif age > max_age_seconds:
                            reason = "stale_readiness"
                        elif payload.status is not OverallStatus.READY or any(
                            check.status is not ComponentStatus.UP
                            for check in payload.checks.values()
                        ):
                            reason = "dependency_unhealthy"
                        else:
                            reason = "ready"
    except (TimeoutError, httpx.TimeoutException):
        reason = "timeout"
    except httpx.HTTPError:
        reason = "transport_error"
    except (ValidationError, ValueError):
        reason = "invalid_readiness"
    return {
        "schema_version": 1,
        "scope": "single-external-readiness-probe",
        "healthy": reason == "ready",
        "reason": reason,
        "http_status": status_code,
        "observed_at": clock().isoformat(),
        "checked_at": checked_at,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "notifications_sent": 0,
        "network_route": "configured-proxy" if proxy else "direct",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--max-age-seconds", type=float, default=120)
    parser.add_argument(
        "--use-system-proxy",
        action="store_true",
        help="Use the existing system HTTPS proxy without exposing its address in the report",
    )
    args = parser.parse_args()
    proxy = None
    if args.use_system_proxy:
        proxy = getproxies().get("https")
        if not proxy:
            parser.exit(2, "system HTTPS proxy is not configured\n")
    try:
        result = asyncio.run(
            probe_readiness(
                args.url,
                timeout_seconds=args.timeout_seconds,
                max_age_seconds=args.max_age_seconds,
                proxy=proxy,
            )
        )
    except ValueError:
        parser.exit(2, "invalid probe configuration\n")
    print(json.dumps(result, sort_keys=True))
    if not result["healthy"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
