from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import DatabasePrincipalResolver, JwtTokenCodec
from enterprise_doc_core.billing import EntitlementUsageService
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.product_models import ProductQuota
from enterprise_doc_core.config import FoundationSettings, ModelProvider, ModelSettings
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.ingestion_server import IngestionHarness

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("execution", ["asgi", "tcp-cli"])
async def test_business_matrix_uploads_three_formats_and_verifies_recovery_ledgers(
    browser_db, tmp_path, monkeypatch, execution
):
    from scripts.business_capacity import load_business_plan, run_business_matrix
    from scripts.business_capacity_observer import CoreBusinessObserver

    settings = FoundationSettings()
    database_url = (
        make_url(settings.database.url.get_secret_value())
        .update_query_dict({"options": f"-csearch_path={browser_db.schema},public"})
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE__URL", database_url)
    monkeypatch.setenv("EMBEDDING__PROVIDER", "hash")
    harness = IngestionHarness(tmp_path)
    runner = None
    server = None
    serving = None
    listener = None
    requests = []
    try:
        await harness.start()
        first = harness.principals[0]
        now = datetime.now(UTC)
        entitlement_id = uuid4()
        async with harness.sessions.begin() as session:
            session.add(
                TenantEntitlement(
                    id=entitlement_id,
                    tenant_id=first.tenant_id,
                    plan_code="capacity-local",
                    version=1,
                    period_start=now - timedelta(minutes=1),
                    period_end=now + timedelta(hours=1),
                    provider_request_limit=100,
                )
            )
            await session.flush()
            for metric, limit in (("document_bytes", 32 * 1024 * 1024), ("agent_task", 100)):
                session.add(
                    ProductQuota(
                        tenant_id=first.tenant_id,
                        entitlement_id=entitlement_id,
                        metric=metric,
                        unit_limit=limit,
                    )
                )

        async def provider(request):
            text = json.loads(json.loads(request.content)["messages"][1]["content"])["requirement"][
                "text"
            ]
            requests.append((request.url.host, text))
            await asyncio.sleep(0.05)
            if "both_routes_failure" in text or (
                "primary_failure" in text and request.url.host == "primary.invalid"
            ):
                return httpx.Response(503)
            return await harness.model_response(request)

        gateways = {
            route: OpenAICompatiblePresalesGateway(
                ModelSettings(
                    provider=ModelProvider.OPENAI_COMPATIBLE,
                    base_url=f"https://{route}.invalid/v1",
                    model_name="controlled-capacity",
                    api_key=SecretStr("test-only"),
                ),
                transport=httpx.MockTransport(provider),
            )
            for route in ("primary", "fallback")
        }
        retriever = HybridRetrievalService(
            session_factory=harness.sessions, embedding_provider=HashEmbeddingProvider()
        )
        service = PresalesService(
            session_factory=harness.sessions,
            retriever=retriever,
            gateway=gateways["primary"],
            settings=PresalesSettings(
                generation_enabled=True,
                background_generation_enabled=True,
                automatic_failover_enabled=True,
                row_timeout_seconds=15,
                model_timeout_seconds=5,
                route_failure_threshold=10,
                daily_attempt_limit=1000,
            ),
            usage_service=EntitlementUsageService(session_factory=harness.sessions),
        )
        worker = BackgroundGeneration(service.generation, gateways)

        async def background():
            while True:
                if not await worker.run_once("local-capacity-worker"):
                    await asyncio.sleep(0.02)

        await harness.resume()
        runner = asyncio.create_task(background())
        app = create_app(settings=harness.settings, presales_service=service)
        principal = await DatabasePrincipalResolver(
            session_factory=harness.sessions, codec=JwtTokenCodec(harness.settings.auth)
        ).resolve(first.token)
        observer = CoreBusinessObserver(
            harness.sessions,
            harness.resources.multipart_object_store,
            harness.settings.object_store.documents_bucket,
            retriever,
            principal,
        )
        cases = []
        for name, fault in (
            ("txt", "none"),
            ("pdf", "primary_failure"),
            ("docx", "both_routes_failure"),
        ):
            fixture = harness.fixtures[name]
            filename = f"source.{name}"
            (tmp_path / filename).write_bytes(fixture.content)
            cases.append(
                {
                    "key": name,
                    "path": filename,
                    "sha256": hashlib.sha256(fixture.content).hexdigest(),
                    "size_bytes": len(fixture.content),
                    "media_type": fixture.media_type,
                    "query": fixture.excerpt.split()[0] + " " + fault,
                    "excerpt": fixture.excerpt,
                    "fault_label": fault,
                    "expected_terminal": "failed" if fault == "both_routes_failure" else "drafted",
                    "min_provider_calls": 1 if fault == "none" else 2,
                }
            )
        plan = {
            "schema_version": 1,
            "base_url": "http://127.0.0.1:18766",
            "object_origins": [harness.settings.object_store.presign_endpoint.rstrip("/")],
            "repetitions": 2,
            "cases": cases,
            "poll_seconds": 0.05,
            "phases": [
                {
                    "name": name,
                    "tasks": 2 if name == "burst" else 1,
                    "concurrency": 2 if name == "burst" else 1,
                }
                for name in ("ramp", "steady_state", "burst", "recovery")
            ],
        }
        plan_path = tmp_path / "business-plan.json"
        if execution == "tcp-cli":
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.setblocking(False)
            plan["base_url"] = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                        pytest.fail("local HTTP server did not start")
                    await asyncio.sleep(0.01)
            environment = dict(os.environ)
            for key in list(environment):
                if key.split("__")[0] in {
                    "DATABASE",
                    "REDIS",
                    "OBJECT_STORE",
                    "AUTH",
                    "MODEL",
                    "EMBEDDING",
                    "APP_ENV",
                }:
                    del environment[key]
            for key, model in (
                ("DATABASE", harness.settings.database),
                ("REDIS", harness.settings.redis),
                ("OBJECT_STORE", harness.settings.object_store),
                ("AUTH", harness.settings.auth),
                ("MODEL", ModelSettings()),
                ("EMBEDDING", harness.settings.embedding),
            ):
                environment[key] = json.dumps(
                    model.model_dump(),
                    default=lambda v: v.get_secret_value() if isinstance(v, SecretStr) else v,
                )
            environment.update(APP_ENV="local", ENTERPRISE_DOC_LOAD_TOKEN=first.token)
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            command = [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-m",
                "scripts.run_business_capacity",
                "--plan",
                str(plan_path),
                "--execute-local",
                "--confirm-controlled-target",
                "--output-dir",
            ]
            output = tmp_path / "cli-output"
            executed = await asyncio.to_thread(
                subprocess.run,
                [*command, str(output)],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=180,
            )
            assert (output / "run.json").is_file(), (
                executed.returncode,
                executed.stdout,
                executed.stderr,
            )
            report = json.loads((output / "run.json").read_text(encoding="utf-8"))
            assert executed.returncode == 0, (executed.stdout, executed.stderr, report)
            assert report["preflight_completed"] and report["local_clients_closed"]
            journal = [
                json.loads(line)
                for line in (output / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            assert sorted(journal, key=lambda s: s["task_index"]) == report["samples"]
            refused_output = tmp_path / "nonempty-tenant"
            refused = await asyncio.to_thread(
                subprocess.run,
                [*command, str(refused_output)],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            assert refused.returncode == 1
            refusal = json.loads((refused_output / "run.json").read_text(encoding="utf-8"))
            assert refusal["error_code"] == "empty_isolated_tenant_required"
            assert refusal["tasks_not_started"] == 10
        else:
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            async with app.router.lifespan_context(app):
                report = await run_business_matrix(
                    load_business_plan(plan_path),
                    first.token,
                    observer,
                    api_transport=httpx.ASGITransport(app),
                )
        (tmp_path / "business-result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        assert report["status"] == "local_checks_passed", [
            (s.get("reason"), s.get("error_type")) for s in report["samples"]
        ]
        assert report["summary"]["planned_tasks"] == 10
        assert report["summary"]["business_successes"] == 7
        assert report["summary"]["expected_failures"] == 3
        assert report["summary"]["boundaries"]["retrieval"]["passed"] == 10
        assert len(requests) == 16
        for sample in report["samples"]:
            assert sample["generation"]["ledger"]["attempts"] == 1
            assert sample["ingestion"]["document_quantity"] == sample["ingestion"]["size_bytes"]
            assert sample["ingestion"]["document_consumptions"] == 1
        assert first.token not in json.dumps(report)
        assert "signature=" not in json.dumps(report)
    finally:
        try:
            if server is not None:
                server.should_exit = True
            if serving is not None:
                await asyncio.wait_for(serving, 10)
        finally:
            if listener is not None:
                listener.close()
            if runner is not None:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            try:
                receipt = await harness.cleanup()
                assert receipt["success"]
            finally:
                await harness.resources.close()
