from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from enterprise_doc_core.analysis.execution_models import (
    AnalysisCellRevision,
    AnalysisDependency,
    AnalysisRun,
    AnalysisUnit,
)
from enterprise_doc_core.analysis.review import AnalysisReviewDeliveryService
from enterprise_doc_core.analysis.review_schemas import (
    ExportInput,
    ManualRevisionInput,
    ReviewInput,
)
from enterprise_doc_core.analysis.schemas import (
    AnalysisField,
    AnalysisParameter,
    AnalysisSpec,
    ComparisonObject,
)
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_models import ProductQuota
from tests.analysis.test_analysis_execution_integration import claim_run, response, setup
from tests.analysis.test_analysis_review_delivery import MemoryArtifactStore

pytestmark = pytest.mark.integration


async def _run_to_terminal(service, sessions, kwargs, *, key: str, base_run_id=None):
    run = await service.create_run(
        **(kwargs | {"idempotency_key": key, "base_run_id": base_run_id})
    )
    async with sessions() as session:
        persisted = await session.get(AnalysisRun, run.id)
        assert persisted is not None
        job_id = persisted.job_id
    if job_id is not None:
        runtime, claim = await claim_run(sessions, run)
        await service.execute(claim)
        await runtime.succeed(claim)
    return run, await service.matrix(
        tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=run.id
    )


async def _update_spec(service, kwargs, spec):
    project = await service.get_project(
        tenant_id=kwargs["tenant_id"],
        actor_id=kwargs["actor_id"],
        project_id=kwargs["project_id"],
    )
    await service.update_project(
        tenant_id=kwargs["tenant_id"],
        actor_id=kwargs["actor_id"],
        project_id=kwargs["project_id"],
        expected_revision=project.revision,
        title=project.title,
        spec=spec,
    )
    snapshot = await service.freeze_sources(
        tenant_id=kwargs["tenant_id"],
        actor_id=kwargs["actor_id"],
        project_id=kwargs["project_id"],
        expected_revision=project.revision + 1,
    )
    return kwargs | {"snapshot_id": snapshot.id}


async def _dependency_setup(sessions, client):
    service, kwargs, _ = await setup(sessions, client, fields=1, quota=30)
    project = await service.get_project(
        tenant_id=kwargs["tenant_id"],
        actor_id=kwargs["actor_id"],
        project_id=kwargs["project_id"],
    )
    source_id = project.spec.objects[0].source_version_ids[0]
    spec = AnalysisSpec(
        objects=(ComparisonObject(id="a", name="Supplier A", source_version_ids=(source_id,)),),
        fields=(
            AnalysisField(
                id="base",
                name="Base",
                question="What is base?",
                parameter_ids=("seats",),
            ),
            AnalysisField(
                id="derived",
                name="Derived",
                question="What is derived?",
                upstream_field_ids=("base",),
            ),
            AnalysisField(id="unrelated", name="Unrelated", question="What is unrelated?"),
        ),
        parameters=(AnalysisParameter(id="seats", name="Seats", value="10", unit="seat"),),
    )
    return service, await _update_spec(service, kwargs, spec), spec


def _unit_map(matrix):
    return {(unit.object_id, unit.field_id): unit for unit in matrix.units}


async def test_unchanged_cross_snapshot_reuses_all_units_without_job_or_model_call(browser_db):
    calls = []

    async def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=2)
        first, first_matrix = await _run_to_terminal(
            service, browser_db.sessions, kwargs, key="first"
        )
        project = await service.get_project(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            project_id=kwargs["project_id"],
        )
        second_kwargs = await _update_spec(service, kwargs, project.spec)
        _second, second_matrix = await _run_to_terminal(
            service,
            browser_db.sessions,
            second_kwargs,
            key="second",
            base_run_id=first.id,
        )

    assert first_matrix.run.dispatches == 2
    assert second_matrix.run.status == "completed"
    assert second_matrix.run.dispatches == 0
    assert second_matrix.run.reused_units == 2
    assert second_matrix.run.affected_units == 0
    assert len(calls) == 2
    assert all(unit.reused_from_unit_id is not None for unit in second_matrix.units)
    assert all(unit.freshness == "current" for unit in second_matrix.units)
    async with browser_db.sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(ProductQuota.metric == ProductMetric.ANALYSIS_CELL)
        )
        assert quota is not None and (quota.units_used, quota.units_reserved) == (2, 0)
        assert await session.scalar(select(func.count()).select_from(AnalysisRun)) == 2


async def test_parameter_change_recomputes_parameter_field_and_downstream_only(browser_db):
    calls = []

    async def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, spec = await _dependency_setup(browser_db.sessions, client)
        first, _ = await _run_to_terminal(service, browser_db.sessions, kwargs, key="first")
        changed_spec = spec.model_copy(
            update={
                "parameters": (
                    AnalysisParameter(id="seats", name="Seats", value="20", unit="seat"),
                )
            }
        )
        changed_kwargs = await _update_spec(service, kwargs, changed_spec)
        _second, matrix = await _run_to_terminal(
            service,
            browser_db.sessions,
            changed_kwargs,
            key="second",
            base_run_id=first.id,
        )

    units = _unit_map(matrix)
    assert matrix.run.reused_units == 1
    assert matrix.run.affected_units == 2
    assert matrix.run.dispatches == 2
    assert units[("a", "unrelated")].reused_from_unit_id is not None
    assert units[("a", "base")].reused_from_unit_id is None
    assert units[("a", "derived")].reused_from_unit_id is None
    assert units[("a", "base")].invalidation_reasons == ("parameter_changed:seats",)
    assert set(units[("a", "derived")].invalidation_reasons) == {
        "parameter_changed:seats",
        "upstream_changed:base",
    }
    assert len(calls) == 5


async def test_review_is_not_inherited_and_manual_upstream_revision_makes_downstream_stale(
    browser_db,
):
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        service, kwargs, spec = await _dependency_setup(browser_db.sessions, client)
        first, matrix = await _run_to_terminal(service, browser_db.sessions, kwargs, key="first")
        review_service = AnalysisReviewDeliveryService(
            session_factory=browser_db.sessions,
            gateway=service.gateway,
            artifact_store=MemoryArtifactStore(),
        )
        base = _unit_map(matrix)[("a", "base")]
        base_revision = await review_service.create_cell_revision(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            unit_id=base.id,
            expected_revision=1,
            idempotency_key="manual-base",
            request=ManualRevisionInput(
                action="final",
                value="manually confirmed",
                evidence_state="insufficient",
                reason="Human correction",
            ),
        )
        await review_service.review_cell_revision(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            unit_id=base.id,
            idempotency_key="approve-base",
            request=ReviewInput(
                revision=base_revision.revision,
                content_hash=base_revision.content_hash,
                decision="approved",
                reason="Reviewed",
            ),
        )
        changed_kwargs = await _update_spec(service, kwargs, spec)
        second, second_matrix = await _run_to_terminal(
            service,
            browser_db.sessions,
            changed_kwargs,
            key="second",
            base_run_id=first.id,
        )
        second_base = _unit_map(second_matrix)[("a", "base")]
        assert second_base.review is None
        assert second_base.current_revision == 1

        with pytest.raises(AnalysisError, match="analysis_review_required"):
            await review_service.create_export(
                tenant_id=kwargs["tenant_id"],
                actor_id=kwargs["actor_id"],
                run_id=second.id,
                idempotency_key="reviewed-second",
                request=ExportInput(mode="reviewed"),
            )

        await review_service.create_cell_revision(
            tenant_id=kwargs["tenant_id"],
            actor_id=kwargs["actor_id"],
            unit_id=base.id,
            expected_revision=2,
            idempotency_key="manual-base-v3",
            request=ManualRevisionInput(
                action="final",
                value="another correction",
                evidence_state="insufficient",
                reason="Second human correction",
            ),
        )
        stale = await review_service.matrix(
            tenant_id=kwargs["tenant_id"], actor_id=kwargs["actor_id"], run_id=first.id
        )
        assert _unit_map(stale)[("a", "derived")].freshness == "stale"
        assert "upstream_changed:base" in _unit_map(stale)[("a", "derived")].invalidation_reasons


async def test_tampered_dependency_history_and_result_are_not_reused(browser_db):
    calls = []

    async def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=1)
        first, _ = await _run_to_terminal(service, browser_db.sessions, kwargs, key="first")
        async with browser_db.sessions.begin() as session:
            unit = await session.scalar(select(AnalysisUnit).where(AnalysisUnit.run_id == first.id))
            assert unit is not None
            dependency = await session.scalar(
                select(AnalysisDependency).where(AnalysisDependency.unit_id == unit.id)
            )
            revision = await session.scalar(
                select(AnalysisCellRevision).where(AnalysisCellRevision.unit_id == unit.id)
            )
            assert dependency is not None and revision is not None
            dependency.fingerprint = "0" * 64
            revision.result = dict(revision.result) | {"reason": "tampered"}
        _second, matrix = await _run_to_terminal(
            service,
            browser_db.sessions,
            kwargs,
            key="second",
            base_run_id=first.id,
        )

    unit = matrix.units[0]
    assert matrix.run.reused_units == 0
    assert matrix.run.affected_units == 1
    assert "dependency_history_invalid" in unit.invalidation_reasons
    assert len(calls) == 2


async def test_tampered_result_is_not_reused_even_when_dependencies_match(browser_db):
    calls = []

    async def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service, kwargs, _ = await setup(browser_db.sessions, client, fields=1)
        first, _ = await _run_to_terminal(service, browser_db.sessions, kwargs, key="first")
        async with browser_db.sessions.begin() as session:
            unit = await session.scalar(select(AnalysisUnit).where(AnalysisUnit.run_id == first.id))
            assert unit is not None
            revision = await session.scalar(
                select(AnalysisCellRevision).where(AnalysisCellRevision.unit_id == unit.id)
            )
            assert revision is not None
            revision.result = dict(revision.result) | {"reason": "tampered"}
        _, matrix = await _run_to_terminal(
            service,
            browser_db.sessions,
            kwargs,
            key="second",
            base_run_id=first.id,
        )

    assert matrix.run.reused_units == 0
    assert matrix.run.affected_units == 1
    assert matrix.units[0].invalidation_reasons == ("previous_result_invalid",)
    assert len(calls) == 2
