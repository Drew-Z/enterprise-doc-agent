import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.analysis.execution import ANALYSIS_JOB_TYPE
from enterprise_doc_core.analysis.execution_models import AnalysisRun
from enterprise_doc_core.analysis.review import AnalysisReviewDeliveryService
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.config.settings import AnalysisSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.jobs.service import JobRuntimeService
from enterprise_doc_worker.analysis_handler import (
    execution_failure_lock_key,
    project_execution_failure,
)
from enterprise_doc_worker.handler import JobHandlerRouter, classify_job_error
from enterprise_doc_worker.queue import JobDeliveryConsumer, JobMessage
from tests.analysis.test_analysis_execution_integration import claim_run, response, setup
from tests.analysis.test_analysis_review_delivery import MemoryArtifactStore

pytestmark = pytest.mark.integration


async def test_api_to_worker_matrix_events_and_disabled_admission(browser_db):
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as provider:
        service, kwargs, _ = await setup(browser_db.sessions, provider, fields=2)

        class Resolver:
            async def resolve(self, token):
                if token != "analysis-test":
                    raise InvalidBearerToken()
                return PrincipalContext(str(kwargs["tenant_id"]), str(kwargs["actor_id"]), "member")

        app = create_app(
            settings=ApiSettings(
                upload=UploadSettings(structured_documents_enabled=True),
                analysis=AnalysisSettings(execution_enabled=True),
            ),
            checkers=(),
            principal_resolver=Resolver(),
            analysis_service=service,
            analysis_execution_service=service,
        )
        headers = {"Authorization": "Bearer analysis-test", "Idempotency-Key": "api-run"}
        route = f"/api/analysis/projects/{kwargs['project_id']}/runs"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            assert (
                await client.post(route, json={"snapshotId": str(kwargs["snapshot_id"])})
            ).status_code == 401
            created = await client.post(
                route, headers=headers, json={"snapshotId": str(kwargs["snapshot_id"])}
            )
            assert created.status_code == 202, created.text
            replay = await client.post(
                route, headers=headers, json={"snapshotId": str(kwargs["snapshot_id"])}
            )
            assert replay.json()["id"] == created.json()["id"]
            async with browser_db.sessions() as session:
                row = await session.scalar(select(AnalysisRun))
                message = JobMessage(
                    job_id=str(row.job_id), tenant_id=str(row.tenant_id), event_id=str(row.id)
                )
            runtime = JobRuntimeService(
                session_factory=browser_db.sessions,
                failure_lock_key=execution_failure_lock_key,
                failure_projector=project_execution_failure,
            )
            consumer = JobDeliveryConsumer(
                runtime=runtime,
                worker_id="api-worker",
                handler=JobHandlerRouter({ANALYSIS_JOB_TYPE: service.execute}),
                classify_error=classify_job_error,
            )
            assert await consumer.handle(message) == "succeeded"
            assert await consumer.handle(message) == "duplicate_or_not_claimable"
            run_route = f"/api/analysis/runs/{created.json()['id']}"
            matrix = await client.get(run_route + "/matrix", headers=headers)
            assert matrix.status_code == 200, matrix.text
            assert matrix.json()["run"]["finishedUnits"] == 2
            assert len(matrix.json()["units"]) == 2
            first = await client.get(run_route + "/events", headers=headers)
            ids = [
                line.split(": ")[1] for line in first.text.splitlines() if line.startswith("id: ")
            ]
            assert ids and "实施费" not in first.text
            resumed = await client.get(
                run_route + "/events", headers=headers | {"Last-Event-ID": ids[-1]}
            )
            assert "id: " not in resumed.text
            app.state.analysis_execution_enabled = False
            assert (
                await client.post(
                    route, headers=headers, json={"snapshotId": str(kwargs["snapshot_id"])}
                )
            ).status_code == 409
            assert (await client.get(run_route + "/matrix", headers=headers)).status_code == 200


async def test_base_run_http_contract_reuses_and_rejects_conflicting_scope(browser_db):
    calls = []

    def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        service, kwargs, _ = await setup(browser_db.sessions, provider, fields=2)
        base = await service.create_run(**kwargs)
        runtime, claim = await claim_run(browser_db.sessions, base)
        await service.execute(claim)
        await runtime.succeed(claim)

        project = await service.get_project(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
        )
        updated = await service.update_project(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
            expected_revision=project.revision,
            title=project.title,
            spec=project.spec,
        )
        snapshot = await service.freeze_sources(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
            expected_revision=updated.revision,
        )
        other_project = await service.create_project(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            title="Other comparison",
            spec=project.spec,
        )
        other_snapshot = await service.freeze_sources(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=other_project.id,
            expected_revision=other_project.revision,
        )

        class Resolver:
            async def resolve(self, token):
                if token != "analysis-test":
                    raise InvalidBearerToken()
                return PrincipalContext(str(kwargs["tenant_id"]), str(kwargs["actor_id"]), "member")

        app = create_app(
            settings=ApiSettings(
                upload=UploadSettings(structured_documents_enabled=True),
                analysis=AnalysisSettings(execution_enabled=True),
            ),
            checkers=(),
            principal_resolver=Resolver(),
            analysis_service=service,
            analysis_execution_service=service,
        )
        auth = {"Authorization": "Bearer analysis-test"}
        route = f"/api/analysis/projects/{kwargs['project_id']}/runs"
        body = {
            "snapshotId": str(snapshot.id),
            "executionMode": "fixed",
            "baseRunId": str(base.id),
        }
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            created = await client.post(
                route,
                headers=auth | {"Idempotency-Key": "api-incremental"},
                json=body,
            )
            assert created.status_code == 202, created.text
            assert created.json()["baseRunId"] == str(base.id)
            assert created.json()["status"] == "completed"
            assert created.json()["reusedUnits"] == 2
            assert created.json()["affectedUnits"] == 0
            assert len(calls) == 2

            conflict = await client.post(
                route,
                headers=auth | {"Idempotency-Key": "api-incremental"},
                json=body | {"baseRunId": created.json()["id"]},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "analysis_idempotency_conflict"

            wrong_mode = await client.post(
                route,
                headers=auth | {"Idempotency-Key": "api-wrong-mode"},
                json=body | {"executionMode": "agent"},
            )
            assert wrong_mode.status_code == 409
            assert wrong_mode.json()["error"]["code"] == "analysis_base_run_invalid"

            wrong_project = await client.post(
                f"/api/analysis/projects/{other_project.id}/runs",
                headers=auth | {"Idempotency-Key": "api-wrong-project"},
                json={
                    "snapshotId": str(other_snapshot.id),
                    "executionMode": "fixed",
                    "baseRunId": str(base.id),
                },
            )
            assert wrong_project.status_code == 409
            assert wrong_project.json()["error"]["code"] == "analysis_base_run_invalid"


async def test_agent_mode_input_submission_is_authenticated_strict_and_replay_safe(browser_db):
    calls = 0

    def input_response(request):
        nonlocal calls
        calls += 1
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        if calls == 1:
            content = {
                "action": "need_input",
                "value": None,
                "evidence_state": "insufficient",
                "reason": "The deployment count is required.",
                "missing_parameters": ["deployment_count"],
            }
        else:
            window = payload["evidence"]["windows"][0]
            assert payload["user_inputs"] == {"deployment_count": "3"}
            content = {
                "action": "final",
                "value": window["text"],
                "evidence_state": "supported",
                "citations": [{"evidence_id": window["id"], "quote": window["text"]}],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"total_tokens": 21},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(input_response)) as provider:
        service, kwargs, _ = await setup(browser_db.sessions, provider)

        class Resolver:
            async def resolve(self, token):
                if token != "analysis-test":
                    raise InvalidBearerToken()
                return PrincipalContext(str(kwargs["tenant_id"]), str(kwargs["actor_id"]), "member")

        app = create_app(
            settings=ApiSettings(
                upload=UploadSettings(structured_documents_enabled=True),
                analysis=AnalysisSettings(execution_enabled=True),
            ),
            checkers=(),
            principal_resolver=Resolver(),
            analysis_service=service,
            analysis_execution_service=service,
        )
        headers = {"Authorization": "Bearer analysis-test", "Idempotency-Key": "api-agent"}
        route = f"/api/analysis/projects/{kwargs['project_id']}/runs"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            created = await client.post(
                route,
                headers=headers,
                json={"snapshotId": str(kwargs["snapshot_id"]), "executionMode": "agent"},
            )
            assert created.status_code == 202, created.text
            assert created.json()["executionMode"] == "agent"
            conflict = await client.post(
                route,
                headers=headers,
                json={"snapshotId": str(kwargs["snapshot_id"]), "executionMode": "fixed"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "analysis_idempotency_conflict"

            async with browser_db.sessions() as session:
                row = await session.scalar(select(AnalysisRun))
                message = JobMessage(
                    job_id=str(row.job_id), tenant_id=str(row.tenant_id), event_id=str(row.id)
                )
            runtime = JobRuntimeService(
                session_factory=browser_db.sessions,
                failure_lock_key=execution_failure_lock_key,
                failure_projector=project_execution_failure,
            )
            consumer = JobDeliveryConsumer(
                runtime=runtime,
                worker_id="api-input-worker",
                handler=JobHandlerRouter({ANALYSIS_JOB_TYPE: service.execute}),
                classify_error=classify_job_error,
            )
            assert await consumer.handle(message) == "succeeded"
            run_route = f"/api/analysis/runs/{created.json()['id']}"
            matrix = await client.get(run_route + "/matrix", headers=headers)
            request = matrix.json()["units"][0]["inputRequest"]
            assert matrix.json()["run"]["status"] == "waiting_input"
            body = {
                "submissions": [
                    {
                        "requestId": request["id"],
                        "expectedRevision": request["revision"],
                        "values": {"deployment_count": "3"},
                    }
                ]
            }
            assert (await client.post(run_route + "/inputs", json=body)).status_code == 401
            strict = await client.post(
                run_route + "/inputs", headers=headers, json=body | {"unexpected": True}
            )
            assert strict.status_code == 422
            forged = await client.post(
                run_route + "/inputs",
                headers=headers,
                json={"submissions": [body["submissions"][0] | {"requestId": str(uuid4())}]},
            )
            assert forged.status_code == 404
            stale = await client.post(
                run_route + "/inputs",
                headers=headers,
                json={
                    "submissions": [
                        body["submissions"][0] | {"expectedRevision": request["revision"] + 1}
                    ]
                },
            )
            assert stale.status_code == 409
            resumed = await client.post(run_route + "/inputs", headers=headers, json=body)
            assert resumed.status_code == 202 and resumed.json()["status"] == "queued"
            replay = await client.post(run_route + "/inputs", headers=headers, json=body)
            assert replay.status_code == 202 and replay.json()["id"] == resumed.json()["id"]
            changed = await client.post(
                run_route + "/inputs",
                headers=headers,
                json={
                    "submissions": [body["submissions"][0] | {"values": {"deployment_count": "4"}}]
                },
            )
            assert changed.status_code == 409


async def test_manual_revision_and_exact_review_http_contract(browser_db):
    store = MemoryArtifactStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as provider:
        execution, kwargs, _ = await setup(browser_db.sessions, provider)
        run = await execution.create_run(**kwargs)
        async with browser_db.sessions() as session:
            row = await session.get(AnalysisRun, run.id)
            assert row is not None and row.job_id is not None
        runtime = JobRuntimeService(session_factory=browser_db.sessions)
        claim = await runtime.claim(job_id=row.job_id, worker_id="review-api-worker")
        assert claim is not None
        await execution.execute(claim)
        await runtime.succeed(claim)
        service = AnalysisReviewDeliveryService(
            session_factory=browser_db.sessions,
            gateway=execution.gateway,
            artifact_store=store,
        )

        class Resolver:
            async def resolve(self, token):
                if token != "analysis-test":
                    raise InvalidBearerToken()
                return PrincipalContext(str(kwargs["tenant_id"]), str(kwargs["actor_id"]), "member")

        app = create_app(
            settings=ApiSettings(
                upload=UploadSettings(structured_documents_enabled=True),
                analysis=AnalysisSettings(execution_enabled=True),
            ),
            checkers=(),
            principal_resolver=Resolver(),
            analysis_service=service,
            analysis_execution_service=service,
        )
        auth = {"Authorization": "Bearer analysis-test"}
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            matrix = (await client.get(f"/api/analysis/runs/{run.id}/matrix", headers=auth)).json()
            unit = matrix["units"][0]
            route = f"/api/analysis/cells/{unit['id']}"
            body = {
                "action": "final",
                "value": "Buyer confirmed value",
                "evidenceState": "insufficient",
                "reason": "Confirmed directly by the buyer.",
                "valueOrigin": "user_provided",
            }
            missing_match = await client.post(
                route + "/revisions",
                headers=auth | {"Idempotency-Key": "manual-v2"},
                json=body,
            )
            assert missing_match.status_code == 428
            stale_zero = await client.post(
                route + "/revisions",
                headers=auth | {"Idempotency-Key": "manual-zero", "If-Match": '"0"'},
                json=body,
            )
            assert stale_zero.status_code == 409
            assert stale_zero.json()["error"]["code"] == "analysis_revision_conflict"
            changed = await client.post(
                route + "/revisions",
                headers=auth | {"Idempotency-Key": "manual-v2", "If-Match": '"1"'},
                json=body,
            )
            assert changed.status_code == 201, changed.text
            assert changed.json()["revision"] == 2
            review_body = {
                "revision": 2,
                "contentHash": changed.json()["contentHash"],
                "decision": "approved",
                "reason": "Checked by the project owner.",
            }
            reviewed = await client.post(
                route + "/reviews",
                headers=auth | {"Idempotency-Key": "approve-v2"},
                json=review_body,
            )
            assert reviewed.status_code == 201, reviewed.text
            history = await client.get(route + "/revisions", headers=auth)
            assert history.status_code == 200
            assert [item["revision"] for item in history.json()] == [2, 1]
            projected = await client.get(f"/api/analysis/runs/{run.id}/matrix", headers=auth)
            assert projected.json()["units"][0]["result"]["reviewState"] == "approved"
            artifact_response = await client.post(
                f"/api/analysis/runs/{run.id}/exports",
                headers=auth | {"Idempotency-Key": "api-reviewed-xlsx"},
                json={"mode": "reviewed"},
            )
            assert artifact_response.status_code == 201, artifact_response.text
            artifact = artifact_response.json()
            listed = await client.get(f"/api/analysis/runs/{run.id}/artifacts", headers=auth)
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()] == [artifact["id"]]
            assert listed.json()[0]["status"] == "ready"
            approval_response = await client.post(
                f"/api/analysis/artifacts/{artifact['id']}/approvals",
                headers=auth | {"Idempotency-Key": "api-approve-artifact"},
                json={
                    "artifactHash": artifact["contentHash"],
                    "sourceManifestHash": artifact["sourceManifestHash"],
                    "matrixHash": artifact["matrixHash"],
                    "operation": "deliver",
                    "decision": "approved",
                    "reason": "Approved through the HTTP delivery flow.",
                    "expiresAt": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                },
            )
            assert approval_response.status_code == 201, approval_response.text
            refreshed = await client.get(f"/api/analysis/runs/{run.id}/artifacts", headers=auth)
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()[0]["status"] == "approved"
            download = await client.get(
                f"/api/analysis/artifacts/{artifact['id']}/download", headers=auth
            )
            assert download.status_code == 200, download.text
            assert download.json()["contentHash"] == artifact["contentHash"]
            assert download.json()["url"].startswith("https://download.test/")
