"""Loopback browser acceptance: real DB, HTTP API and worker; synthetic provider HTTP."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Header, HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.config import DatabaseSettings, ModelProvider, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesProviderCall
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from tests.agent.test_agent_run_integration import _seed_agent_context
from tests.browser_sessions.conftest import browser_db
from tests.presales.fixtures import add_chunk
from tests.presales.test_presales_background_integration import valid_response


async def main() -> None:
    async with asynccontextmanager(browser_db.__wrapped__)() as db:
        context, other = (
            await _seed_agent_context(db.sessions),
            await _seed_agent_context(db.sessions),
        )
        await add_chunk(
            db.sessions,
            context,
            context.document_version_id,
            context.generation_id,
            "Retention is 30 days.",
        )
        now = datetime.now(UTC)
        async with db.sessions.begin() as session:
            session.add(
                TenantEntitlement(
                    tenant_id=context.tenant_id,
                    plan_code="browser-test",
                    version=1,
                    period_start=now - timedelta(minutes=1),
                    period_end=now + timedelta(hours=1),
                    provider_request_limit=100,
                    created_at=now,
                    updated_at=now,
                )
            )
        release = asyncio.Event()
        repaired = False
        requests: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.host)
            await release.wait()
            text = json.loads(json.loads(request.content)["messages"][1]["content"])["requirement"][
                "text"
            ]
            if "transient" in text and request.url.host == "primary.invalid":
                return httpx.Response(503)
            if "terminal" in text and not repaired:
                return httpx.Response(400)
            return valid_response(request)

        models = {
            route: OpenAICompatiblePresalesGateway(
                ModelSettings(
                    provider=ModelProvider.OPENAI_COMPATIBLE,
                    base_url=f"https://{route}.invalid/v1",
                    model_name="browser-fixture",
                    api_key=SecretStr("test-only"),
                ),
                transport=httpx.MockTransport(respond),
            )
            for route in ("primary", "fallback")
        }
        service = PresalesService(
            session_factory=db.sessions,
            retriever=HybridRetrievalService(
                session_factory=db.sessions, embedding_provider=HashEmbeddingProvider()
            ),
            gateway=models["primary"],
            settings=PresalesSettings(
                generation_enabled=True,
                background_generation_enabled=True,
                automatic_failover_enabled=True,
                row_timeout_seconds=180,
            ),
            usage_service=EntitlementUsageService(session_factory=db.sessions),
        )
        worker = BackgroundGeneration(service.generation, models)
        token, other_token = uuid4().hex, uuid4().hex

        class Resolver:
            async def resolve(self, candidate: str) -> PrincipalContext:
                if candidate == token:
                    return context.principal
                if candidate == other_token:
                    return other.principal
                raise InvalidBearerToken()

        url = (
            make_url(DatabaseSettings().url.get_secret_value())
            .update_query_dict({"options": f"-csearch_path={db.schema},public"})
            .render_as_string(hide_password=False)
        )
        app = create_app(
            settings=ApiSettings(_env_file=None, database=DatabaseSettings(url=SecretStr(url))),
            checkers=[],
            principal_resolver=Resolver(),
            presales_service=service,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18765, access_log=False, log_level="warning")
        )

        async def poll() -> None:
            while True:
                if not await worker.run_once("browser-worker"):
                    await asyncio.sleep(0.1)

        runner = asyncio.create_task(poll())

        def guard(value: str | None) -> None:
            if value != "presales-browser":
                raise HTTPException(403)

        @app.get("/__presales_test__/context")
        async def test_context(x_presales_test: str | None = Header(default=None)) -> dict:
            guard(x_presales_test)
            return {"token": token, "otherToken": other_token}

        @app.post("/__presales_test__/release")
        async def allow(x_presales_test: str | None = Header(default=None)) -> dict:
            guard(x_presales_test)
            release.set()
            return {"released": True}

        @app.post("/__presales_test__/repair")
        async def repair(x_presales_test: str | None = Header(default=None)) -> dict:
            nonlocal repaired
            guard(x_presales_test)
            repaired = True
            return {"repaired": True}

        @app.get("/__presales_test__/stats")
        async def stats(x_presales_test: str | None = Header(default=None)) -> dict:
            guard(x_presales_test)
            async with db.sessions() as session:
                reservations = (await session.scalars(select(UsageReservation))).all()
                calls = (await session.scalars(select(PresalesProviderCall))).all()
                return {
                    "requests": requests,
                    "calls": len(calls),
                    "consumed": sum(r.state == "consumed" for r in reservations),
                    "released": sum(r.state == "released" for r in reservations),
                }

        @app.post("/__presales_test__/shutdown")
        async def shutdown(x_presales_test: str | None = Header(default=None)) -> dict:
            guard(x_presales_test)
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            async with db.sessions.begin() as session:
                await session.execute(
                    delete(Tenant).where(Tenant.id.in_((context.tenant_id, other.tenant_id)))
                )
                await session.execute(
                    delete(User).where(User.id.in_((context.actor_id, other.actor_id)))
                )
                remaining = await session.scalar(select(func.count()).select_from(Tenant))
            server.should_exit = True
            return {"remainingTestTenants": remaining}

        try:
            await server.serve()
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=selector_event_loop_factory)
