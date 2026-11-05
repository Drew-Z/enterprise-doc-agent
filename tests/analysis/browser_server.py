"""Real local API/DB/Worker with synthetic evidence and controlled provider HTTP."""

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Header, HTTPException
from pydantic import SecretStr
from sqlalchemy import select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.analysis.gateway import AnalysisGateway
from enterprise_doc_core.analysis.review import AnalysisReviewDeliveryService
from enterprise_doc_core.analysis.service import AnalysisWorkspaceService
from enterprise_doc_core.config import ModelSettings, UploadSettings
from enterprise_doc_core.config.settings import AnalysisSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.documents import DocumentInventoryService
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.jobs.service import JobRuntimeService
from enterprise_doc_worker.analysis_handler import (
    execution_failure_lock_key,
    project_execution_failure,
)
from enterprise_doc_worker.handler import classify_job_error
from enterprise_doc_worker.queue import JobDeliveryConsumer, JobMessage
from tests.analysis.test_analysis_review_delivery import MemoryArtifactStore
from tests.analysis.test_source_workspace_integration import seed_source
from tests.browser_sessions.conftest import browser_db


async def main() -> None:
    database_fixture = browser_db.__wrapped__()
    database = await anext(database_fixture)
    cleaned = False
    worker_task = None
    provider = None
    controlled_calls = 0
    try:
        tenant, actor, version, _ = await seed_source(database.sessions)
        token = "analysis-browser-" + uuid4().hex

        def controlled_response(request):
            nonlocal controlled_calls
            controlled_calls += 1
            body = json.loads(json.loads(request.content)["messages"][1]["content"])
            field = body["field"]["id"]
            window = body["evidence"]["windows"][0]
            if field == "setup_fee":
                result = {
                    "action": "final",
                    "value": window["text"],
                    "evidence_state": "supported",
                    "citations": [{"evidence_id": window["id"], "quote": window["text"]}],
                }
            else:
                result = {
                    "action": "abstain",
                    "evidence_state": "insufficient",
                    "reason": "合成测试资料未提供这一项的充分证据。",
                }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
                    ]
                },
            )

        provider = httpx.AsyncClient(transport=httpx.MockTransport(controlled_response))
        analysis_settings = AnalysisSettings(
            execution_enabled=True,
            unit_deadline_seconds=30,
            run_deadline_seconds=300,
            max_input_bytes=65536,
        )
        gateway = AnalysisGateway(
            model=ModelSettings(
                provider="openai_compatible",
                base_url="https://controlled.example.test/v1",
                api_key=SecretStr("fixture-only"),
                model_name="controlled",
                route_deadline_seconds=30,
            ),
            settings=analysis_settings,
            client=provider,
        )
        artifact_store = MemoryArtifactStore()
        execution = AnalysisReviewDeliveryService(
            session_factory=database.sessions,
            gateway=gateway,
            artifact_store=artifact_store,
        )
        runtime = JobRuntimeService(
            session_factory=database.sessions,
            failure_lock_key=execution_failure_lock_key,
            failure_projector=project_execution_failure,
        )
        consumer = JobDeliveryConsumer(
            runtime=runtime,
            worker_id="analysis-browser-worker",
            handler=execution.execute,
            classify_error=classify_job_error,
        )

        async def consume():
            while True:
                async with database.sessions() as session:
                    jobs = (
                        await session.scalars(
                            select(Job).where(
                                Job.type == "analysis.execute",
                                Job.status.in_(["pending", "retry_wait"]),
                            )
                        )
                    ).all()
                for job in jobs:
                    await consumer.handle(
                        JobMessage(
                            job_id=str(job.id), tenant_id=str(job.tenant_id), event_id=str(job.id)
                        )
                    )
                await asyncio.sleep(0.1)

        worker_task = asyncio.create_task(consume())

        class Resolver:
            async def resolve(self, candidate: str) -> PrincipalContext:
                if candidate != token:
                    raise InvalidBearerToken()
                return PrincipalContext(str(tenant), str(actor), "member")

        app = create_app(
            settings=ApiSettings(
                _env_file=None,
                upload=UploadSettings(structured_documents_enabled=True),
                analysis=analysis_settings,
            ),
            principal_resolver=Resolver(),
            checkers=(),
            document_inventory_service=DocumentInventoryService(session_factory=database.sessions),
            analysis_service=AnalysisWorkspaceService(session_factory=database.sessions),
            analysis_execution_service=execution,
        )

        def require_header(value: str | None) -> None:
            if value != "analysis-browser":
                raise HTTPException(403)

        @app.get("/__analysis_test__/context")
        async def context(x_analysis_test: str | None = Header(default=None)) -> dict:
            require_header(x_analysis_test)
            return {"token": token, "versionId": str(version)}

        @app.post("/__analysis_test__/cleanup")
        async def cleanup(x_analysis_test: str | None = Header(default=None)) -> dict:
            nonlocal cleaned
            require_header(x_analysis_test)
            if not cleaned:
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
                await database_fixture.aclose()
                cleaned = True
            return {
                "isolatedSchemaRemoved": cleaned,
                "modelCalls": 0,
                "controlledModelCalls": controlled_calls,
            }

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18766, access_log=False, log_level="warning")
        )
        await server.serve()
    finally:
        if worker_task is not None and not worker_task.done():
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        if provider is not None:
            await provider.aclose()
        if not cleaned:
            await database_fixture.aclose()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=selector_event_loop_factory) as runner:
        runner.run(main())
