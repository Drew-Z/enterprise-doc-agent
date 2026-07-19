from __future__ import annotations

from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from enterprise_doc_core.telemetry import MetricsRuntime


class MetricsMiddleware:
    """Observe HTTP requests without putting identifiers into metric labels."""

    def __init__(self, app: ASGIApp, *, metrics: MetricsRuntime, enabled: bool = True) -> None:
        self.app = app
        self.metrics = metrics
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope.get("type") != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", None)
            self.metrics.observe_api(
                method=str(scope.get("method", "OTHER")),
                route=route_path if isinstance(route_path, str) else None,
                status_code=status_code,
                duration=perf_counter() - started,
            )
