"""Real Keycloak plus the production API; test controls remain a separate origin."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.identity.models import ExternalIdentityBinding, User
from tests.first_use.browser_server import check_startup, control_app, serve_apps
from tests.first_use.harness import FirstUseHarness
from tests.first_use.keycloak_fixture import KeycloakFixture


class KeycloakProductHarness(FirstUseHarness):
    def __init__(self, output: Path, identity: KeycloakFixture) -> None:
        super().__init__(output, keycloak_auth=identity.settings)
        self.identity = identity

    async def snapshot(self) -> dict[str, Any]:
        result = await super().snapshot()
        state = await asyncio.to_thread(self.identity.snapshot)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        ExternalIdentityBinding.issuer, ExternalIdentityBinding.subject, User.email
                    ).join(User, User.id == ExternalIdentityBinding.user_id)
                )
            ).all()
        state.update(
            bindingCount=len(rows),
            bindingsMatchKeycloak=all(
                issuer == self.identity.issuer and self.identity.subjects.get(email) == subject
                for issuer, subject, email in rows
            ),
        )
        result.pop("protocol")
        result.update(identityProvider="keycloak", keycloak=state)
        return result


def identity_controls(harness: KeycloakProductHarness, stop: asyncio.Event) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def protect(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/test/") and not secrets.compare_digest(
            request.headers.get("X-Invitation-Run", ""), harness.run_id
        ):
            return JSONResponse({"error": "test_control_forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ready", "identityProvider": "keycloak"}

    @app.get("/test/state")
    async def state() -> dict[str, Any]:
        return await harness.snapshot()

    @app.post("/test/shutdown")
    async def shutdown() -> dict[str, object]:
        stop.set()
        return {"stopping": True, "runId": harness.run_id}

    @app.get("/test/keycloak/account/{account}")
    async def account_context(account: Literal["owner", "desktop"]) -> dict[str, object]:
        return await asyncio.to_thread(harness.identity.browser_context, account)

    @app.get("/test/keycloak/mail/{account}")
    async def mail_link(
        account: Literal["owner", "desktop"], offset: int = Query(ge=0, le=20)
    ) -> dict[str, object]:
        return await asyncio.to_thread(harness.identity.mail_link, account, offset)

    @app.get("/test/keycloak/snapshot")
    async def identity_snapshot() -> dict[str, Any]:
        if not harness.migrated:
            raise HTTPException(503)
        return await harness.snapshot()

    return control_app(harness, stop, base_app=app)


async def main(output: Path) -> None:
    await asyncio.to_thread(check_startup, output)
    identity = KeycloakFixture(output)
    harness: KeycloakProductHarness | None = None
    try:
        await asyncio.to_thread(identity.start)
        harness = KeycloakProductHarness(output, identity)
        await harness.start()
        stop = asyncio.Event()
        await serve_apps(harness, identity_controls(harness, stop), stop)
    finally:
        try:
            if harness is not None:
                await harness.cleanup()
        finally:
            await asyncio.to_thread(identity.close)


if __name__ == "__main__":
    destination = Path(os.environ["FIRST_USE_OUTPUT_DIR"]).resolve(strict=True)
    asyncio.run(main(destination), loop_factory=selector_event_loop_factory)
