from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from enterprise_doc_core.analysis.execution import AnalysisExecutionService
from enterprise_doc_core.analysis.execution_models import (
    AnalysisAction,
    AnalysisCellRevision,
    AnalysisInputRequest,
    AnalysisRun,
    AnalysisToolExecution,
    AnalysisUnit,
)
from enterprise_doc_core.analysis.gateway import AnalysisGateway
from enterprise_doc_core.analysis.schemas import (
    AnalysisField,
    AnalysisSpec,
    ComparisonObject,
    default_fields,
)
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
    ProductQuotaConfiguration,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_models import (
    ProductQuota,
    ProductUsageEvent,
    ProductUsageReservation,
)
from enterprise_doc_core.billing.provider_calls import ProviderCallService, recorded_post
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import ModelSettings, ProviderUsageSettings
from enterprise_doc_core.config.settings import AnalysisSettings
from enterprise_doc_core.documents.models import (
    Document,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.documents.structure import parse_document_structure
from enterprise_doc_core.documents.structure_store import persist_structure
from enterprise_doc_core.jobs.models import Job, OutboxEvent
from enterprise_doc_core.jobs.service import JobRuntimeService
from enterprise_doc_core.presales.models import PresalesDispatchDay
from enterprise_doc_core.uploads.models import UploadSession
from tests.analysis.test_source_workspace_integration import seed_source

pytestmark = pytest.mark.integration


def response(request):
    payload = json.loads(json.loads(request.content)["messages"][1]["content"])
    window = payload["evidence"]["windows"][0]
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "final",
                                "value": window["text"],
                                "evidence_state": "supported",
                                "citations": [
                                    {"evidence_id": window["id"], "quote": window["text"]}
                                ],
                            }
                        )
                    },
                }
            ],
            "usage": {"total_tokens": 21},
        },
    )


async def setup(sessions, client, *, fields=1, quota=30, provider_settings=None, value_type="text"):
    tenant, actor, version, document = await seed_source(sessions)
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions, clock=lambda: now).configure(
        tenant_id=tenant,
        operator=PlatformEntitlementOperator("analysis-test", "configure analysis cells"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="analysis-test",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=10,
            analysis_cell_limit=quota,
        ),
    )
    gateway = AnalysisGateway(
        model=ModelSettings(
            provider="openai_compatible",
            base_url="https://primary.example.test/v1",
            api_key=SecretStr("test-only"),
            model_name="frozen-primary",
            route_deadline_seconds=30,
            fallback_provider="openai_compatible",
            fallback_base_url="https://must-not-call.example.test/v1",
            fallback_api_key=SecretStr("test-only"),
            fallback_model_name="fallback",
        ),
        settings=AnalysisSettings(
            unit_deadline_seconds=30, run_deadline_seconds=300, max_input_bytes=65536
        ),
        client=client,
    )
    service = AnalysisExecutionService(
        session_factory=sessions, gateway=gateway, provider_settings=provider_settings
    )
    project = await service.create_project(
        tenant_id=tenant,
        actor_id=actor,
        title="Comparison",
        spec=AnalysisSpec(
            objects=(ComparisonObject(id="a", name="Supplier A", source_version_ids=(version,)),),
            fields=tuple(
                AnalysisField(
                    id=f"field_{i}", name="实施费", question="实施费多少?", value_type=value_type
                )
                for i in range(fields)
            ),
        ),
    )
    snapshot = await service.freeze_sources(
        tenant_id=tenant, actor_id=actor, project_id=project.id, expected_revision=1
    )
    kwargs = dict(
        tenant_id=tenant,
        actor_id=actor,
        project_id=project.id,
        snapshot_id=snapshot.id,
        idempotency_key="stable-run",
    )
    return service, kwargs, document


async def claim_run(sessions, run):
    async with sessions() as session:
        row = await session.get(AnalysisRun, run.id)
        job_id = row.job_id
    runtime = JobRuntimeService(session_factory=sessions)
    claim = await runtime.claim(job_id=job_id, worker_id="analysis-test")
    assert claim
    return runtime, claim


async def test_analysis_quota_append_preserves_both_configuration_receipts(browser_db):
    tenant, _, _, _ = await seed_source(browser_db.sessions)
    now = datetime.now(UTC)
    service = EntitlementAdministrationService(
        session_factory=browser_db.sessions, clock=lambda: now
    )
    operator = PlatformEntitlementOperator("analysis-test", "enable analysis cells")
    period_id = uuid4()
    period = EntitlementConfiguration(
        entitlement_id=period_id,
        expected_version=0,
        plan_code="analysis-test",
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=1),
        provider_request_limit=10,
    )
    assert not (
        await service.configure(tenant_id=tenant, operator=operator, configuration=period)
    ).replayed

    products = ProductQuotaConfiguration(
        entitlement_id=period_id,
        expected_version=1,
        agent_task_limit=0,
        document_bytes_limit=0,
        analysis_cell_limit=30,
    )
    assert not (
        await service.configure_products(
            tenant_id=tenant, operator=operator, configuration=products
        )
    ).replayed
    assert (
        await service.configure(tenant_id=tenant, operator=operator, configuration=period)
    ).replayed
    replay = await service.configure_products(
        tenant_id=tenant, operator=operator, configuration=products
    )
    assert replay.replayed
    assert {quota.metric: quota.limit for quota in replay.entitlement.product_quotas} == {
        ProductMetric.AGENT_TASK: 0,
        ProductMetric.DOCUMENT_BYTES: 0,
        ProductMetric.ANALYSIS_CELL: 30,
    }


async def test_same_route_retry_is_bounded_atomic_and_settles_once(browser_db):
    calls = []

    async def handle(request):
        calls.append(str(request.url))
        async with browser_db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ProviderDispatch)) == len(
                calls
            )
            assert await session.scalar(select(func.count()).select_from(AnalysisAction)) == len(
                calls
            )
        return httpx.Response(503) if len(calls) == 1 else response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        first, second = await asyncio.gather(
            service.create_run(**kwargs), service.create_run(**kwargs)
        )
        assert first.id == second.id
        runtime, claim = await claim_run(browser_db.sessions, first)
        await service.execute(claim)
        await service.execute(claim)
        await runtime.succeed(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=first.id
        )
        assert matrix.run.status == "completed"
        assert matrix.run.dispatches == 2
        assert len(calls) == 2 and len(set(calls)) == 1
        assert matrix.units[0].result.evidence[0].locator.line == 1
        async with browser_db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == 1
            assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
            assert await session.scalar(select(func.count()).select_from(AnalysisCellRevision)) == 1
            quota = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert (quota.units_used, quota.units_reserved) == (1, 0)
            assert await session.scalar(select(func.count()).select_from(ProductUsageEvent)) == 1


@pytest.mark.parametrize(
    "failure,expected_calls,code",
    [
        ("timeout", 1, "analysis_response_unknown"),
        ("busy", 2, "analysis_provider_busy"),
        ("forbidden", 1, "analysis_provider_rejected"),
        ("invalid", 1, "analysis_output_invalid"),
        ("connect", 2, "analysis_connect_failed"),
    ],
)
async def test_failure_classes_do_not_fallback_or_sample_until_success(
    browser_db, failure, expected_calls, code
):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if failure == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        if failure == "connect":
            raise httpx.ConnectError("test", request=request)
        if failure == "invalid":
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "wrong"}}]}
            )
        return httpx.Response(429 if failure == "busy" else 403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "failed"
        assert matrix.units[0].error_code == code
        assert matrix.units[0].result is None
        assert len(calls) == expected_calls and set(calls) == {"primary.example.test"}
        async with browser_db.sessions() as session:
            quota = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert (quota.units_used, quota.units_reserved) == (0, 0)


async def test_cancel_during_http_keeps_cost_and_rejects_late_result(browser_db):
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        started.set()
        await release.wait()
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=2)
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        task = asyncio.create_task(service.execute(claim))
        await asyncio.wait_for(started.wait(), 5)
        await service.cancel(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        release.set()
        await task
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "cancelled"
        assert matrix.run.dispatches == 1
        assert all(u.result is None and u.status == "cancelled" for u in matrix.units)


async def test_crash_receipt_is_unknown_and_new_worker_does_not_send_again(browser_db):
    started = asyncio.Event()
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        run = await service.create_run(**kwargs)
        runtime, claim = await claim_run(browser_db.sessions, run)
        task = asyncio.create_task(service.execute(claim))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with browser_db.sessions.begin() as session:
            job = await session.get(Job, claim.job_id)
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        new_claim = await runtime.claim(job_id=claim.job_id, worker_id="recovered-worker")
        assert new_claim and new_claim.fencing_token != claim.fencing_token
        await service.execute(new_claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.units[0].error_code == "analysis_response_unknown"
        assert count == 1


async def test_admission_rolls_back_all_units_when_quota_is_insufficient(browser_db):
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=2, quota=1)
        with pytest.raises(UsageError, match="usage_limit_reached"):
            await service.create_run(**kwargs)
        async with browser_db.sessions() as session:
            for model in (AnalysisRun, AnalysisUnit, Job, OutboxEvent, ProductUsageReservation):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            quota_row = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert quota_row.units_reserved == 0


async def test_permissions_are_rechecked_before_each_dispatch_and_matrix_read(browser_db):
    calls = []

    async def handle(request):
        calls.append(request)
        async with browser_db.sessions.begin() as session:
            doc = await session.get(Document, document)
            doc.access_mode = "restricted"
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, document = await setup(browser_db.sessions, client, fields=2)
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        assert len(calls) == 1
        with pytest.raises(AnalysisError, match="analysis_not_found"):
            await service.matrix(
                tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
            )
        async with browser_db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AnalysisCellRevision)) == 0


async def test_legacy_generation_budget_and_stricter_provider_limit_are_shared(browser_db):
    calls = []

    def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=2)
        async with browser_db.sessions.begin() as session:
            session.add(PresalesDispatchDay(day=datetime.now(UTC).date(), dispatched=199))
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "partial" and matrix.run.dispatches == 1
        assert matrix.units[1].error_code == "provider_generation_budget_exhausted"
        assert len(calls) == 1
        async with browser_db.sessions() as session:
            quota = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert (quota.units_used, quota.units_reserved) == (1, 0)


async def test_stricter_aggregate_budget_is_checked_at_every_dispatch(browser_db):
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        service, kwargs, _ = await setup(
            browser_db.sessions,
            client,
            fields=2,
            provider_settings=ProviderUsageSettings(daily_call_limit=1),
        )
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "partial" and matrix.run.dispatches == 1
        assert matrix.units[1].error_code == "provider_daily_budget_exhausted"


async def test_thirty_cell_matrix_keeps_supplier_and_shared_requirement_scopes_separate(browser_db):
    observed = []

    def handle(request):
        body = json.loads(json.loads(request.content)["messages"][1]["content"])
        observed.append((body["comparison_object"], body["evidence"]["searched_versions"]))
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, document_id = await setup(browser_db.sessions, client)
        async with browser_db.sessions.begin() as session:
            original = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
            document = await session.get(Document, document_id)
            upload = await session.get(UploadSession, original.upload_session_id)
            versions = [original.id]
            for label, amount in (("B", 2400), ("C", 3600), ("requirements", 9999)):
                doc_id, version_id, upload_id = uuid4(), uuid4(), uuid4()
                content = f"Supplier {label}: Setup price {amount}.\nTax excluded.".encode()
                digest = hashlib.sha256(content).hexdigest()
                object_key = f"test/{version_id}/{label}.txt"

                def copy_columns(model, source, **overrides):
                    values = {
                        column.name: getattr(source, column.name)
                        for column in model.__table__.columns
                    }
                    return model(**(values | overrides))

                copied_upload = copy_columns(
                    UploadSession,
                    upload,
                    id=upload_id,
                    document_version_id=None,
                    pending_document_id=doc_id,
                    pending_version_id=version_id,
                    idempotency_key=str(upload_id),
                    object_key=object_key,
                    declared_sha256=digest,
                    request_fingerprint=digest,
                    size_bytes=len(content),
                )
                session.add(copied_upload)
                session.add(copy_columns(Document, document, id=doc_id, title=label))
                await session.flush()
                session.add(
                    copy_columns(
                        DocumentVersion,
                        original,
                        id=version_id,
                        document_id=doc_id,
                        upload_session_id=upload_id,
                        object_key=object_key,
                        original_filename=label + ".txt",
                        declared_sha256=digest,
                        size_bytes=len(content),
                    )
                )
                await session.flush()
                copied_upload.document_version_id = version_id
                generation = DocumentIngestionGeneration(
                    tenant_id=kwargs["tenant_id"],
                    document_version_id=version_id,
                    parser_version=2,
                    chunker_version=1,
                    embedding_version=2,
                    status="succeeded",
                    stage="ready",
                    active=True,
                )
                session.add(generation)
                await session.flush()
                await persist_structure(
                    session,
                    tenant_id=kwargs["tenant_id"],
                    document_version_id=version_id,
                    generation_id=generation.id,
                    parsed=parse_document_structure(content, extension=".txt"),
                )
                versions.append(version_id)
        objects = tuple(
            ComparisonObject(id=label.lower(), name=label, source_version_ids=(version,))
            for label, version in zip(("A", "B", "C"), versions[:3], strict=True)
        )
        await service.update_project(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
            expected_revision=1,
            title="Three suppliers",
            spec=AnalysisSpec(
                common_requirements=(versions[3],), objects=objects, fields=default_fields()
            ),
        )
        snapshot = await service.freeze_sources(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
            expected_revision=2,
        )
        kwargs["snapshot_id"] = snapshot.id
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.total_units == matrix.run.finished_units == len(matrix.units) == 30
        assert matrix.run.dispatches == len(observed) == 30
        for name, ids in observed:
            assert ids == [str(versions[("A", "B", "C").index(name)])]
        assert all(str(versions[3]) not in ids for _, ids in observed)
        assert all(unit.result is not None for unit in matrix.units)


async def test_fixed_and_agent_modes_share_snapshot_route_and_two_slot_budget(browser_db):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        can_use_tools = "You may request one supplied batch" in body["messages"][0]["content"]
        requests.append(
            {
                "model": body["model"],
                "object": payload["comparison_object"],
                "searched_versions": payload["evidence"]["searched_versions"],
                "can_use_tools": can_use_tools,
                "tool_results": len(payload["tool_results"]),
            }
        )
        if can_use_tools:
            content = {
                "action": "tools",
                "reason": "Exercise the bounded frozen-source search.",
                "tools": [{"tool": "search_sources", "query": "实施费", "max_results": 1}],
            }
        else:
            window = (
                payload["tool_results"][0]["evidence"]["windows"][0]
                if payload["tool_results"]
                else payload["evidence"]["windows"][0]
            )
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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        fixed = await service.create_run(**kwargs)
        fixed_runtime, fixed_claim = await claim_run(browser_db.sessions, fixed)
        await service.execute(fixed_claim)
        await fixed_runtime.succeed(fixed_claim)

        agent = await service.create_run(
            **(kwargs | {"idempotency_key": "stable-agent"}), execution_mode="agent"
        )
        agent_runtime, agent_claim = await claim_run(browser_db.sessions, agent)
        await service.execute(agent_claim)
        await agent_runtime.succeed(agent_claim)

        fixed_matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=fixed.id
        )
        agent_matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=agent.id
        )
        assert fixed.snapshot_id == agent.snapshot_id == kwargs["snapshot_id"]
        assert fixed.execution_mode == "fixed" and agent.execution_mode == "agent"
        assert fixed.max_dispatches == agent.max_dispatches == 2
        assert fixed_matrix.run.status == agent_matrix.run.status == "completed"
        assert fixed_matrix.run.dispatches == 1 and agent_matrix.run.dispatches == 2
        assert fixed_matrix.units[0].result is not None
        assert agent_matrix.units[0].result is not None
        assert fixed_matrix.units[0].result.evidence[0].document_version_id == (
            agent_matrix.units[0].result.evidence[0].document_version_id
        )
        async with browser_db.sessions() as session:
            agent_tools = await session.scalar(
                select(func.count())
                .select_from(AnalysisToolExecution)
                .join(AnalysisUnit, AnalysisUnit.id == AnalysisToolExecution.unit_id)
                .where(AnalysisUnit.run_id == agent.id)
            )
            fixed_tools = await session.scalar(
                select(func.count())
                .select_from(AnalysisToolExecution)
                .join(AnalysisUnit, AnalysisUnit.id == AnalysisToolExecution.unit_id)
                .where(AnalysisUnit.run_id == fixed.id)
            )
        assert fixed_tools == 0 and agent_tools == 1
        assert [item["model"] for item in requests] == ["frozen-primary"] * 3
        assert requests[0]["searched_versions"] == requests[1]["searched_versions"]
        assert [item["can_use_tools"] for item in requests] == [False, True, False]
        assert [item["tool_results"] for item in requests] == [0, 0, 1]


async def test_intent_association_failure_rolls_back_before_http(browser_db):
    tenant, _, _, _ = await seed_source(browser_db.sessions)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async def guard(session):
        pass

    async def fail_link(session, dispatch):
        raise AnalysisError("analysis_link_failed")

    service = ProviderCallService(session_factory=browser_db.sessions)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with service.scope(
            tenant_id=tenant, operation_id=uuid4(), kind="agent", guard=guard, intent_link=fail_link
        ):
            with pytest.raises(AnalysisError, match="analysis_link_failed"):
                await recorded_post(
                    client,
                    "https://primary.example.test",
                    provider="test",
                    model="test",
                    json_body={},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                )
    assert calls == []
    async with browser_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ProviderDispatch)) == 0


async def test_truncated_sse_is_unknown_and_never_retried(browser_db):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"id":"partial","choices":[{"index":0,"delta":{"content":"{"}}]}\n\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        service.gateway.policy = service.gateway.policy.model_copy(update={"streaming": True})
        run = await service.create_run(**kwargs)
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.units[0].error_code == "analysis_response_unknown"
        assert len(calls) == 1 and matrix.units[0].result is None


async def test_waiting_input_is_persisted_without_lease_or_budget_reset(browser_db):
    def handle(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "need_input",
                                    "evidence_state": "insufficient",
                                    "reason": "Quantity needed",
                                    "missing_parameters": ["User count"],
                                }
                            )
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        run = await service.create_run(**kwargs)
        runtime, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        await runtime.succeed(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "waiting_input" and matrix.run.dispatches == 1
        async with browser_db.sessions() as session:
            assert (await session.get(Job, claim.job_id)).lease_token is None
            quota = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert (quota.units_used, quota.units_reserved) == (0, 1)
        await service.cancel(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        async with browser_db.sessions() as session:
            quota = await session.scalar(
                select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
            )
            assert (quota.units_used, quota.units_reserved) == (0, 0)


async def test_calculation_pauses_for_input_and_resumes_with_original_budget_and_deadline(
    browser_db,
):
    calls = []

    def handle(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            content = {
                "action": "tools",
                "reason": "Known price needs user count and period.",
                "tools": [
                    {
                        "tool": "calculate",
                        "operation": "explicit_period_total",
                        "operands": [
                            {
                                "name": "monthly_price",
                                "value": "1200",
                                "unit": "CNY",
                                "evidence_id": "e1",
                            },
                            {"name": "users", "parameter": "user_count", "unit": "seat"},
                            {"name": "months", "parameter": "month_count", "unit": "month"},
                        ],
                        "result_unit": "CNY",
                        "rounding": "half_even",
                        "decimal_places": 2,
                    }
                ],
            }
        else:
            calculation = payload["tool_results"][0]["calculation"]
            assert payload["user_inputs"] == {"month_count": "12", "user_count": "10"}
            content = {
                "action": "final",
                "value": {"amount": calculation["result"], "currency": "CNY"},
                "evidence_state": "supported",
                "citations": [{"evidence_id": "e1", "quote": "1200"}],
                "calculation_id": calculation["id"],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"total_tokens": 21},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, value_type="money")
        run = await service.create_run(**kwargs, execution_mode="agent")
        original_deadline = run.deadline_at
        runtime, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        await runtime.succeed(claim)
        waiting = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        request = waiting.units[0].input_request
        assert waiting.run.status == "waiting_input"
        assert waiting.run.dispatches == 1
        assert request is not None and request.can_resume
        assert [item.name for item in request.parameters] == ["user_count", "month_count"]
        async with browser_db.sessions() as session:
            tool = await session.scalar(select(AnalysisToolExecution))
            assert tool is not None and tool.state == "waiting_input"
            first_job_id = (await session.get(AnalysisRun, run.id)).job_id

        resumed = await service.submit_inputs(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            run_id=run.id,
            submissions=(
                {
                    "request_id": request.id,
                    "expected_revision": request.revision,
                    "values": {"user_count": "10", "month_count": "12"},
                },
            ),
        )
        replay = await service.submit_inputs(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            run_id=run.id,
            submissions=(
                {
                    "request_id": request.id,
                    "expected_revision": request.revision,
                    "values": {"user_count": "10", "month_count": "12"},
                },
            ),
        )
        assert resumed.deadline_at == replay.deadline_at == original_deadline
        async with browser_db.sessions() as session:
            row = await session.get(AnalysisRun, run.id)
            assert row.job_id != first_job_id
            assert await session.scalar(select(func.count()).select_from(Job)) == 2
        runtime2, claim2 = await claim_run(browser_db.sessions, resumed)
        await service.execute(claim2)
        await runtime2.succeed(claim2)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert matrix.run.status == "completed" and matrix.run.dispatches == 2
        assert matrix.units[0].result.value.amount == "144000.00"
        assert matrix.units[0].result.calculations[0].expression == "1200 * 10 * 12"
        assert len(calls) == 2
        async with browser_db.sessions() as session:
            stored = await session.scalar(select(AnalysisInputRequest))
            assert stored is not None and stored.state == "submitted"
            tool = await session.scalar(select(AnalysisToolExecution))
            assert tool is not None and tool.state == "completed"


async def test_terminal_slot_input_request_is_visible_but_not_resumable(browser_db):
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "need_input",
                                    "evidence_state": "insufficient",
                                    "reason": "Quantity needed after retry.",
                                    "missing_parameters": ["user_count"],
                                }
                            )
                        },
                    }
                ],
                "usage": {"total_tokens": 21},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        run = await service.create_run(**kwargs, execution_mode="agent")
        runtime, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        await runtime.succeed(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        request = matrix.units[0].input_request
        assert matrix.run.status == "waiting_input" and matrix.run.dispatches == 2
        assert request is not None and not request.can_resume
        with pytest.raises(AnalysisError, match="analysis_dispatch_limit"):
            await service.submit_inputs(
                tenant_id=kwargs["tenant_id"],
                actor_id=kwargs["actor_id"],
                run_id=run.id,
                submissions=(
                    {
                        "request_id": request.id,
                        "expected_revision": request.revision,
                        "values": {"user_count": "10"},
                    },
                ),
            )


async def test_retry_consumes_terminal_slot_and_cannot_open_a_tool_round(browser_db):
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "tools",
                                    "reason": "Try another search.",
                                    "tools": [
                                        {"tool": "search_sources", "query": "implementation fee"}
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client)
        run = await service.create_run(**kwargs, execution_mode="agent")
        _, claim = await claim_run(browser_db.sessions, run)
        await service.execute(claim)
        matrix = await service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
        )
        assert calls == 2 and matrix.units[0].status == "failed"
        assert matrix.units[0].error_code == "analysis_terminal_action_required"
        async with browser_db.sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AnalysisToolExecution)) == 0
            )
