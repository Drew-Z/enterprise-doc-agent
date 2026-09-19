"""Separate loopback IdP/model controls and the unmodified production API."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from enterprise_doc_api.app import create_app
from enterprise_doc_core.db import selector_event_loop_factory
from tests.first_use.harness import FirstUseHarness
from tests.invitations.browser_server import idp_app


class ConfigurePeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    request_limit: int = Field(strict=True, ge=0, le=100)


def control_app(
    harness: FirstUseHarness, stop: asyncio.Event, *, base_app: FastAPI | None = None
) -> FastAPI:
    app = base_app if base_app is not None else idp_app(harness, stop)

    def require_run(request: Request) -> None:
        if request.headers.get("X-Invitation-Run") != harness.run_id:
            raise HTTPException(403)

    @app.get("/test/context")
    async def context(request: Request) -> dict[str, object]:
        require_run(request)
        return {
            "admissionCodes": {
                key: value.get_secret_value() for key, value in harness.admission_codes.items()
            },
            "fixtures": {key: value.metadata() for key, value in harness.fixtures.items()},
        }

    @app.get("/test/files/{name}")
    async def file(name: str, request: Request) -> Response:
        require_run(request)
        if name not in harness.fixtures:
            raise HTTPException(404)
        fixture = harness.fixtures[name]
        return Response(fixture.content, media_type=fixture.media_type)

    @app.post("/test/configure")
    async def configure(body: ConfigurePeriod, request: Request) -> dict[str, object]:
        require_run(request)
        try:
            return await harness.configure(body.tenant_id, body.request_limit)
        except ValueError as error:
            raise HTTPException(400, "Invalid fixture tenant or period.") from error

    @app.post("/test/publisher/{action}")
    async def publisher(action: Literal["pause", "resume"], request: Request) -> dict[str, object]:
        require_run(request)
        if action == "pause":
            await harness.pause()
        else:
            await harness.resume()
        return {"publisherRunning": bool(harness.publisher_tasks)}

    @app.post("/test/model/fail-next")
    async def fail_next(request: Request) -> dict[str, object]:
        require_run(request)
        harness.fail_next_model = True
        return {"armed": True}

    @app.post("/model/v1/chat/completions")
    async def model(request: Request) -> Response:
        expected = "Bearer " + harness.model_key.get_secret_value()
        if not secrets.compare_digest(request.headers.get("Authorization", ""), expected):
            raise HTTPException(401)
        if harness.fail_next_model:
            harness.fail_next_model = False
            harness.model_requests.append({"status": 503, "controlledFailure": True})
            return Response(
                '{"error":{"code":"controlled_fixture_failure"}}',
                status_code=503,
                media_type="application/json",
            )
        content = await request.body()
        if len(content) > 1024 * 1024:
            raise HTTPException(413)
        response = await harness.model.respond(
            httpx.Request("POST", str(request.url), content=content)
        )
        harness.model_requests.append({"status": response.status_code, "controlledFailure": False})
        return Response(
            response.content, status_code=response.status_code, media_type="application/json"
        )

    return app


def check_startup(output: Path) -> None:
    if not output.is_dir() or any(
        (output / name).exists()
        for name in ("run-context.json", "cleanup.json", "database-evidence.json")
    ):
        raise RuntimeError("Use a fresh dedicated FIRST_USE_OUTPUT_DIR.")
    for port in (5173, 18769, 18770):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(f"First-use port {port} is already occupied.") from error


async def serve_apps(harness: FirstUseHarness, controls: FastAPI, stop: asyncio.Event) -> None:
    servers: list[uvicorn.Server] = []
    try:
        for app, port in (
            (create_app(settings=harness.settings), 18769),
            (controls, 18770),
        ):
            servers.append(
                uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=port,
                        access_log=False,
                        proxy_headers=False,
                        log_level="warning",
                    )
                )
            )
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
    finally:
        for server in servers:
            server.should_exit = True


async def main(output: Path) -> None:
    await asyncio.to_thread(check_startup, output)
    harness = FirstUseHarness(output)
    try:
        await harness.start()
        stop = asyncio.Event()
        await serve_apps(harness, control_app(harness, stop), stop)
    finally:
        await harness.cleanup()


if __name__ == "__main__":
    destination = Path(os.environ["FIRST_USE_OUTPUT_DIR"]).resolve(strict=True)
    asyncio.run(main(destination), loop_factory=selector_event_loop_factory)
