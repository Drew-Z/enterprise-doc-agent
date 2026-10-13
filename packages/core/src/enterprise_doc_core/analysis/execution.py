from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.analysis.dependencies import (
    DependencyFingerprint,
    dependency_fingerprint,
    dependency_fingerprints,
    invalidation_reasons,
    ordered_fields,
)
from enterprise_doc_core.analysis.evidence import select_evidence, validate_candidate
from enterprise_doc_core.analysis.execution_models import (
    AnalysisAction,
    AnalysisCellRevision,
    AnalysisDependency,
    AnalysisEvent,
    AnalysisInputRequest,
    AnalysisReview,
    AnalysisRun,
    AnalysisToolExecution,
    AnalysisUnit,
)
from enterprise_doc_core.analysis.execution_schemas import (
    CalculateRequest,
    CalculationReceipt,
    Candidate,
    CellResult,
    EventView,
    EvidenceSet,
    EvidenceWindow,
    ExecutionPolicy,
    InputParameter,
    InputRequestView,
    InputSubmission,
    MatrixView,
    ReadSourceRequest,
    ReviewView,
    RunStatus,
    RunView,
    SearchSourcesRequest,
    ToolDecision,
    UnitStatus,
    UnitView,
)
from enterprise_doc_core.analysis.gateway import AnalysisGateway, ModelFailure
from enterprise_doc_core.analysis.models import AnalysisSourceSnapshot
from enterprise_doc_core.analysis.schemas import (
    AnalysisField,
    FrozenSource,
    SnapshotView,
    canonical_hash,
)
from enterprise_doc_core.analysis.service import AnalysisError, AnalysisWorkspaceService
from enterprise_doc_core.analysis.tools import (
    FrozenStructures,
    ToolExecutionError,
    ToolInputRequired,
    calculate,
    read_source,
    search_sources,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.generation_budget import require_generation_budget
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.product_contracts import ProductMetric
from enterprise_doc_core.billing.product_usage import ProductUsageService
from enterprise_doc_core.billing.provider_calls import ProviderCallService
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment, FoundationSettings, ProviderUsageSettings
from enterprise_doc_core.documents.ingestion import DocumentParseViolation
from enterprise_doc_core.documents.structure import ParsedDocumentStructure
from enterprise_doc_core.documents.structure_store import load_structure, structure_digest
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.jobs.service import ClaimedJob, JobLeaseLost, create_job_records

ANALYSIS_JOB_TYPE = "analysis.execute"
CLOSED_UNITS = {"succeeded", "failed", "cancelled", "expired"}
ExecutionMode = Literal["fixed", "agent"]


def build_execution_service(
    *, session_factory: async_sessionmaker[AsyncSession], settings: FoundationSettings
) -> AnalysisExecutionService:
    try:
        gateway = AnalysisGateway(model=settings.model, settings=settings.analysis)
    except AnalysisError:
        gateway = None
    return AnalysisExecutionService(
        session_factory=session_factory,
        gateway=gateway,
        app_env=settings.app_env,
        provider_settings=settings.provider_usage,
    )


async def lock_run(session: AsyncSession, run_id: UUID) -> None:
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"analysis-run:{run_id}"},
    )


async def append_event(
    session: AsyncSession,
    run: AnalysisRun,
    event_type: str,
    unit: AnalysisUnit | None = None,
    code: str | None = None,
) -> None:
    sequence = (
        await session.scalar(
            select(func.max(AnalysisEvent.sequence)).where(AnalysisEvent.run_id == run.id)
        )
        or 0
    ) + 1
    session.add(
        AnalysisEvent(
            tenant_id=run.tenant_id,
            run_id=run.id,
            sequence=sequence,
            event_type=event_type,
            unit_id=unit.id if unit else None,
            code=code,
        )
    )
    await session.flush()


async def aggregate_run(session: AsyncSession, run: AnalysisRun) -> None:
    await session.flush()
    states = list(
        (
            await session.scalars(select(AnalysisUnit.status).where(AnalysisUnit.run_id == run.id))
        ).all()
    )
    if run.cancel_requested:
        run.status = "cancelled"
    elif any(state in {"queued", "running"} for state in states):
        run.status = "running"
    elif "waiting_input" in states:
        run.status = "waiting_input"
    elif all(state == "succeeded" for state in states):
        run.status = "completed"
    elif "succeeded" in states:
        run.status = "partial"
    elif "expired" in states:
        run.status = "expired"
    else:
        run.status = "failed"


async def finish_unit(
    session: AsyncSession,
    run: AnalysisRun,
    unit: AnalysisUnit,
    *,
    status: str,
    code: str | None = None,
    result: CellResult | None = None,
) -> None:
    if unit.status in CLOSED_UNITS:
        return
    if result is not None:
        revision = (
            await session.scalar(
                select(func.max(AnalysisCellRevision.revision)).where(
                    AnalysisCellRevision.unit_id == unit.id
                )
            )
            or 0
        ) + 1
        session.add(
            AnalysisCellRevision(
                tenant_id=run.tenant_id,
                unit_id=unit.id,
                revision=revision,
                result=result.model_dump(mode="json"),
                content_hash=canonical_hash(result),
                author_type="model",
                reason="Generated by the frozen analysis route.",
            )
        )
    if status != "waiting_input":
        await ProductUsageService.finish_in_session(
            session,
            tenant_id=run.tenant_id,
            operation_id=unit.id,
            metric=ProductMetric.ANALYSIS_CELL,
            source=str(run.policy.get("prompt_version", "analysis.fixed.v1")),
            consume=status == "succeeded",
            require_reservation=False,
        )
    unit.status = status
    unit.error_code = code
    if result is not None:
        if status == "succeeded":
            unit.freshness = "current"
        await refresh_downstream_dependencies(session, run, unit)
    await append_event(session, run, "unit.finished", unit, code)
    await aggregate_run(session, run)


async def refresh_downstream_dependencies(
    session: AsyncSession,
    run: AnalysisRun,
    upstream: AnalysisUnit,
) -> None:
    revision = await session.scalar(
        select(AnalysisCellRevision)
        .where(AnalysisCellRevision.unit_id == upstream.id)
        .order_by(AnalysisCellRevision.revision.desc())
        .limit(1)
    )
    fingerprint = revision.content_hash if revision is not None else "missing"
    dependencies = (
        await session.scalars(
            select(AnalysisDependency)
            .join(AnalysisUnit, AnalysisUnit.id == AnalysisDependency.unit_id)
            .where(
                AnalysisUnit.run_id == run.id,
                AnalysisDependency.kind == "upstream_cell",
                AnalysisDependency.dependency_key == f"{upstream.object_id}:{upstream.field_id}",
            )
        )
    ).all()
    for dependency in dependencies:
        dependency.fingerprint = fingerprint
        dependent = await session.get(AnalysisUnit, dependency.unit_id)
        if dependent is not None:
            rows = (
                await session.scalars(
                    select(AnalysisDependency)
                    .where(AnalysisDependency.unit_id == dependent.id)
                    .order_by(AnalysisDependency.kind, AnalysisDependency.dependency_key)
                )
            ).all()
            dependent.input_fingerprint = dependency_fingerprint(
                DependencyFingerprint(row.kind, row.dependency_key, row.fingerprint) for row in rows
            )


async def mark_downstream_stale(
    session: AsyncSession,
    run: AnalysisRun,
    upstream: AnalysisUnit,
) -> None:
    dependencies = (
        await session.scalars(
            select(AnalysisDependency)
            .join(AnalysisUnit, AnalysisUnit.id == AnalysisDependency.unit_id)
            .where(
                AnalysisUnit.run_id == run.id,
                AnalysisDependency.kind == "upstream_cell",
                AnalysisDependency.dependency_key == f"{upstream.object_id}:{upstream.field_id}",
            )
        )
    ).all()
    for dependency in dependencies:
        dependent = await session.get(AnalysisUnit, dependency.unit_id)
        if dependent is not None:
            reason = f"upstream_changed:{upstream.field_id}"
            dependent.freshness = "stale"
            dependent.invalidation_reasons = sorted(
                set(dependent.invalidation_reasons or ()) | {reason}
            )


async def project_failure(session: AsyncSession, claim: ClaimedJob, *, code: str) -> None:
    """Called by JobRuntime after the shared run advisory lock, before terminal commit."""
    run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.job_id == claim.job_id, AnalysisRun.tenant_id == claim.tenant_id)
        .with_for_update()
    )
    if run is None:
        return
    units = (
        await session.scalars(
            select(AnalysisUnit)
            .where(AnalysisUnit.run_id == run.id)
            .order_by(AnalysisUnit.ordinal)
            .with_for_update()
        )
    ).all()
    for unit in units:
        if unit.status not in CLOSED_UNITS and unit.status != "waiting_input":
            await finish_unit(session, run, unit, status="failed", code=code)


class AnalysisExecutionService(AnalysisWorkspaceService):
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: AnalysisGateway | None = None,
        app_env: AppEnvironment = AppEnvironment.LOCAL,
        provider_settings: ProviderUsageSettings | None = None,
    ) -> None:
        super().__init__(session_factory=session_factory)
        self.gateway = gateway
        self.usage = ProductUsageService(session_factory=session_factory, app_env=app_env)
        settings = provider_settings or ProviderUsageSettings()
        # Preserve a stricter configured aggregate limit. No analysis-only ledger kind.
        self.calls = ProviderCallService(
            session_factory=session_factory,
            settings=settings.model_copy(
                update={
                    "daily_call_limit": min(200, settings.daily_call_limit),
                    "agent_call_limit": min(2, settings.agent_call_limit),
                }
            ),
        )

    async def create_run(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        project_id: UUID,
        snapshot_id: UUID,
        idempotency_key: str,
        execution_mode: ExecutionMode = "fixed",
        base_run_id: UUID | None = None,
    ) -> RunView:
        if not 1 <= len(idempotency_key) <= 128 or any(
            ord(c) < 33 or ord(c) > 126 for c in idempotency_key
        ):
            raise AnalysisError("analysis_idempotency_invalid")
        async with self.session_factory.begin() as session:
            await lock_usage_tenant(session, tenant_id)
            project = await self._project(session, tenant_id, actor_id, project_id, lock=True)
            previous = await session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.tenant_id == tenant_id,
                    AnalysisRun.project_id == project_id,
                    AnalysisRun.idempotency_key == idempotency_key,
                )
            )
            if previous is not None:
                previous_policy = ExecutionPolicy.model_validate(previous.policy)
                if (
                    previous.snapshot_id != snapshot_id
                    or previous_policy.execution_mode != execution_mode
                    or previous.base_run_id != base_run_id
                ):
                    raise AnalysisError("analysis_idempotency_conflict")
                await self._authorized_snapshot(session, previous, actor_id)
                return await self._run_view(session, previous)
            if self.gateway is None:
                raise AnalysisError("analysis_policy_unconfigured")
            snapshot = await session.scalar(
                select(AnalysisSourceSnapshot).where(
                    AnalysisSourceSnapshot.id == snapshot_id,
                    AnalysisSourceSnapshot.tenant_id == tenant_id,
                    AnalysisSourceSnapshot.project_id == project_id,
                )
            )
            if snapshot is None:
                raise AnalysisError("analysis_not_found")
            if snapshot.project_revision != project.draft_revision:
                raise AnalysisError("analysis_revision_conflict")
            view = await self._snapshot_view(session, snapshot, actor_id)
            policy = self.gateway.policy
            if execution_mode == "agent":
                policy = policy.model_copy(
                    update={
                        "schema_version": "analysis.execution.v2",
                        "prompt_version": "analysis.agent.v1",
                        "execution_mode": "agent",
                        "max_tool_rounds": 1,
                    }
                )
            policy_hash = canonical_hash(policy)
            base_run: AnalysisRun | None = None
            base_units: dict[tuple[str, str], AnalysisUnit] = {}
            if base_run_id is not None:
                base_run = await self._find_run(session, tenant_id, base_run_id)
                if (
                    base_run.project_id != project_id
                    or base_run.status
                    not in {"completed", "partial", "failed", "cancelled", "expired"}
                    or ExecutionPolicy.model_validate(base_run.policy).execution_mode
                    != execution_mode
                ):
                    raise AnalysisError("analysis_base_run_invalid")
                await self._authorized_snapshot(session, base_run, actor_id)
                base_units = {
                    (unit.object_id, unit.field_id): unit
                    for unit in (
                        await session.scalars(
                            select(AnalysisUnit)
                            .where(AnalysisUnit.run_id == base_run.id)
                            .order_by(AnalysisUnit.ordinal)
                        )
                    ).all()
                }
            now = datetime.now(UTC)
            run = AnalysisRun(
                id=uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                snapshot_id=snapshot_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                policy=policy.model_dump(mode="json"),
                policy_hash=policy_hash,
                source_manifest_hash=snapshot.source_manifest_hash,
                base_run_id=base_run_id,
                total_units=len(view.payload.spec.objects) * len(view.payload.spec.fields),
                deadline_at=now + timedelta(seconds=policy.run_deadline_seconds),
            )
            session.add(run)
            await session.flush()
            ordinal = 0
            queued_unit_ids: list[UUID] = []
            for obj in view.payload.spec.objects:
                current_hashes: dict[str, str | None] = {}
                for field in ordered_fields(view):
                    dependencies = dependency_fingerprints(
                        view,
                        object_id=obj.id,
                        field_id=field.id,
                        policy_hash=policy_hash,
                        upstream_content_hashes=current_hashes,
                    )
                    fingerprint = dependency_fingerprint(dependencies)
                    base_unit = base_units.get((obj.id, field.id))
                    base_dependencies = (
                        tuple(
                            DependencyFingerprint(row.kind, row.dependency_key, row.fingerprint)
                            for row in (
                                await session.scalars(
                                    select(AnalysisDependency)
                                    .where(AnalysisDependency.unit_id == base_unit.id)
                                    .order_by(
                                        AnalysisDependency.kind,
                                        AnalysisDependency.dependency_key,
                                    )
                                )
                            ).all()
                        )
                        if base_unit is not None
                        else ()
                    )
                    reasons = (
                        invalidation_reasons(base_dependencies, dependencies)
                        if base_run is not None
                        else ()
                    )
                    reason_set = set(reasons)
                    if base_unit is not None:
                        if (
                            not base_dependencies
                            or dependency_fingerprint(base_dependencies)
                            != base_unit.input_fingerprint
                        ):
                            reason_set.add("dependency_history_invalid")
                        if base_unit.freshness == "stale":
                            reason_set.update(
                                base_unit.invalidation_reasons or ("upstream_changed",)
                            )
                        if base_unit.status != "succeeded":
                            reason_set.add("previous_result_unavailable")
                    reasons = tuple(sorted(reason_set))
                    base_revision = None
                    if base_unit is not None and not reasons:
                        candidate_revision = await session.scalar(
                            select(AnalysisCellRevision)
                            .where(AnalysisCellRevision.unit_id == base_unit.id)
                            .order_by(AnalysisCellRevision.revision.desc())
                            .limit(1)
                        )
                        try:
                            parsed_revision = (
                                CellResult.model_validate(candidate_revision.result)
                                if candidate_revision is not None
                                else None
                            )
                        except ValidationError:
                            parsed_revision = None
                        if (
                            candidate_revision is None
                            or parsed_revision is None
                            or parsed_revision.review_state != "unreviewed"
                            or self._payload_hash(candidate_revision.result)
                            != candidate_revision.content_hash
                        ):
                            reasons = ("previous_result_invalid",)
                        else:
                            try:
                                self._evidence(base_unit)
                            except (AnalysisError, ValidationError):
                                reasons = ("previous_result_invalid",)
                            else:
                                base_revision = candidate_revision
                    reusable = base_revision is not None and base_unit is not None
                    reusable_unit = base_unit if reusable else None
                    unit = AnalysisUnit(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        run_id=run.id,
                        object_id=obj.id,
                        field_id=field.id,
                        ordinal=ordinal,
                        status="succeeded" if reusable else "queued",
                        evidence_set=(reusable_unit.evidence_set if reusable_unit else None),
                        evidence_hash=(reusable_unit.evidence_hash if reusable_unit else None),
                        input_fingerprint=fingerprint,
                        freshness="current" if reusable else ("stale" if base_run else "current"),
                        invalidation_reasons=list(reasons),
                        reused_from_unit_id=reusable_unit.id if reusable_unit else None,
                    )
                    ordinal += 1
                    session.add(unit)
                    await session.flush()
                    session.add_all(
                        [
                            AnalysisDependency(
                                tenant_id=tenant_id,
                                unit_id=unit.id,
                                kind=item.kind,
                                dependency_key=item.key,
                                fingerprint=item.fingerprint,
                            )
                            for item in dependencies
                        ]
                    )
                    if reusable:
                        assert base_revision is not None
                        session.add(
                            AnalysisCellRevision(
                                tenant_id=tenant_id,
                                unit_id=unit.id,
                                revision=1,
                                result=dict(base_revision.result),
                                content_hash=base_revision.content_hash,
                                author_type=base_revision.author_type,
                                actor_id=base_revision.actor_id,
                                reason="Reused from an unchanged dependency fingerprint.",
                                reused_from_revision_id=base_revision.id,
                            )
                        )
                        current_hashes[field.id] = base_revision.content_hash
                    else:
                        await self.usage.reserve(
                            tenant_id=tenant_id,
                            operation_id=unit.id,
                            metric=ProductMetric.ANALYSIS_CELL,
                            session=session,
                        )
                        queued_unit_ids.append(unit.id)
                        current_hashes[field.id] = None
            await session.flush()
            if queued_unit_ids:
                await require_generation_budget(session, now=now)
                daily = await session.scalar(
                    select(func.count())
                    .select_from(ProviderDispatch)
                    .where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.started_at
                        >= now.replace(hour=0, minute=0, second=0, microsecond=0),
                    )
                )
                if (daily or 0) >= self.calls.settings.daily_call_limit:
                    raise UsageError("provider_daily_budget_exhausted")
                job = await create_job_records(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    job_type=ANALYSIS_JOB_TYPE,
                    idempotency_key=f"analysis:{run.id}",
                    payload={
                        "run_id": str(run.id),
                        "unit_ids": [str(value) for value in queued_unit_ids],
                    },
                    max_attempts=3,
                )
                run.job_id = job.job_id
            else:
                run.status = "completed"
            await append_event(session, run, "run.created")
            await self._audit(session, tenant_id, actor_id, "analysis.run.created", run.id)
            return await self._run_view(session, run)

    async def list_runs(
        self, *, tenant_id: UUID, actor_id: UUID, project_id: UUID
    ) -> tuple[RunView, ...]:
        async with self.session_factory() as session:
            await self._project(session, tenant_id, actor_id, project_id)
            rows = (
                await session.scalars(
                    select(AnalysisRun)
                    .where(AnalysisRun.tenant_id == tenant_id, AnalysisRun.project_id == project_id)
                    .order_by(AnalysisRun.created_at.desc())
                    .limit(100)
                )
            ).all()
            views = []
            for run in rows:
                try:
                    await self._authorized_snapshot(session, run, actor_id)
                except AnalysisError as error:
                    if error.code != "analysis_not_found":
                        raise
                    continue
                views.append(await self._run_view(session, run))
            return tuple(views)

    async def matrix(self, *, tenant_id: UUID, actor_id: UUID, run_id: UUID) -> MatrixView:
        async with self.session_factory() as session:
            run = await self._find_run(session, tenant_id, run_id)
            snapshot = await self._authorized_snapshot(session, run, actor_id)
            units = (
                await session.scalars(
                    select(AnalysisUnit)
                    .where(AnalysisUnit.run_id == run.id)
                    .order_by(AnalysisUnit.ordinal)
                )
            ).all()
            result = []
            for unit in units:
                cell = await session.scalar(
                    select(AnalysisCellRevision)
                    .where(AnalysisCellRevision.unit_id == unit.id)
                    .order_by(AnalysisCellRevision.revision.desc())
                    .limit(1)
                )
                parsed = CellResult.model_validate(cell.result) if cell else None
                if cell and parsed and self._payload_hash(cell.result) != cell.content_hash:
                    raise AnalysisError("analysis_result_invalid")
                review = (
                    await session.scalar(
                        select(AnalysisReview).where(
                            AnalysisReview.cell_revision_id == cell.id,
                            AnalysisReview.tenant_id == tenant_id,
                        )
                    )
                    if cell is not None
                    else None
                )
                if parsed is not None and review is not None:
                    parsed = parsed.model_copy(
                        update={
                            "review_state": (
                                "approved" if review.decision == "approved" else "changes_requested"
                            )
                        }
                    )
                input_row = await self._input_request(session, unit.id)
                dispatches = await self._dispatch_count(session, unit.id)
                result.append(
                    UnitView(
                        id=unit.id,
                        object_id=unit.object_id,
                        field_id=unit.field_id,
                        status=cast(UnitStatus, unit.status),
                        dispatches=dispatches,
                        error_code=unit.error_code,
                        result=parsed,
                        search=self._evidence(unit),
                        input_request=(
                            self._input_view(
                                input_row,
                                run,
                                unit,
                                has_dispatch_slot=dispatches
                                < min(2, self.calls.settings.agent_call_limit),
                            )
                            if input_row is not None
                            else None
                        ),
                        current_revision=cell.revision if cell is not None else None,
                        current_revision_id=cell.id if cell is not None else None,
                        current_content_hash=cell.content_hash if cell is not None else None,
                        author_type=cast(Literal["model", "user"], cell.author_type)
                        if cell is not None
                        else None,
                        review=self._review_view(review) if review is not None else None,
                        freshness=cast(Literal["current", "stale"], unit.freshness),
                        invalidation_reasons=tuple(unit.invalidation_reasons or ()),
                        reused_from_unit_id=unit.reused_from_unit_id,
                    )
                )
            return MatrixView(
                run=await self._run_view(session, run), snapshot=snapshot, units=tuple(result)
            )

    async def submit_inputs(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        run_id: UUID,
        submissions: tuple[InputSubmission | dict[str, object], ...],
    ) -> RunView:
        parsed = tuple(
            item if isinstance(item, InputSubmission) else InputSubmission.model_validate(item)
            for item in submissions
        )
        if not 1 <= len(parsed) <= 30 or len({item.request_id for item in parsed}) != len(parsed):
            raise AnalysisError("analysis_input_invalid")
        async with self.session_factory.begin() as session:
            await lock_usage_tenant(session, tenant_id)
            await lock_run(session, run_id)
            run = await self._find_run(session, tenant_id, run_id)
            await self._authorized_snapshot(session, run, actor_id)
            now = datetime.now(UTC)
            if run.cancel_requested:
                raise AnalysisError("analysis_input_closed")

            resume_ids: list[UUID] = []
            for submission in parsed:
                request = await session.scalar(
                    select(AnalysisInputRequest)
                    .where(
                        AnalysisInputRequest.id == submission.request_id,
                        AnalysisInputRequest.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if request is None:
                    raise AnalysisError("analysis_input_not_found")
                unit = await session.scalar(
                    select(AnalysisUnit)
                    .where(
                        AnalysisUnit.id == request.unit_id,
                        AnalysisUnit.run_id == run_id,
                        AnalysisUnit.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if unit is None:
                    raise AnalysisError("analysis_input_not_found")
                expected = {str(item["name"]) for item in request.parameters}
                values = {key: value.strip() for key, value in submission.values.items()}
                if (
                    request.revision != submission.expected_revision
                    or set(values) != expected
                    or any(not value for value in values.values())
                ):
                    raise AnalysisError("analysis_input_revision_conflict")
                submission_hash = self._payload_hash(values)
                if request.state == "submitted":
                    if request.submission_hash != submission_hash:
                        raise AnalysisError("analysis_input_revision_conflict")
                    continue
                if run.deadline_at <= now:
                    raise AnalysisError("analysis_expired")
                if run.status != "waiting_input":
                    raise AnalysisError("analysis_input_busy")
                if request.state != "open" or unit.status != "waiting_input":
                    raise AnalysisError("analysis_input_closed")
                if await self._dispatch_count(session, unit.id) >= min(
                    2, self.calls.settings.agent_call_limit
                ):
                    raise AnalysisError("analysis_dispatch_limit")
                if unit.deadline_at is None or unit.deadline_at <= now:
                    raise AnalysisError("analysis_expired")
                await self.usage.require_reserved(
                    tenant_id=tenant_id,
                    operation_id=unit.id,
                    metric=ProductMetric.ANALYSIS_CELL,
                    session=session,
                )
                request.state = "submitted"
                request.values = values
                request.submission_hash = submission_hash
                request.submitted_by = actor_id
                request.submitted_at = now
                unit.status = "queued"
                unit.error_code = None
                resume_ids.append(unit.id)
                await append_event(session, run, "input.submitted", unit)

            if resume_ids:
                token = hashlib.sha256(
                    ":".join(sorted(str(value) for value in resume_ids)).encode()
                ).hexdigest()[:24]
                job = await create_job_records(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    job_type=ANALYSIS_JOB_TYPE,
                    idempotency_key=f"analysis:{run.id}:resume:{token}",
                    payload={
                        "run_id": str(run.id),
                        "unit_ids": [str(value) for value in resume_ids],
                    },
                    max_attempts=3,
                )
                run.job_id = job.job_id
                run.status = "queued"
                await append_event(session, run, "run.resumed")
                await self._audit(session, tenant_id, actor_id, "analysis.run.resumed", run.id)
            return await self._run_view(session, run)

    async def events(
        self, *, tenant_id: UUID, actor_id: UUID, run_id: UUID, after: int = 0
    ) -> tuple[EventView, ...]:
        async with self.session_factory() as session:
            run = await self._find_run(session, tenant_id, run_id)
            await self._authorized_snapshot(session, run, actor_id)
            rows = (
                await session.scalars(
                    select(AnalysisEvent)
                    .where(AnalysisEvent.run_id == run.id, AnalysisEvent.sequence > after)
                    .order_by(AnalysisEvent.sequence)
                    .limit(100)
                )
            ).all()
            return tuple(
                EventView(
                    sequence=row.sequence,
                    event_type=row.event_type,
                    unit_id=row.unit_id,
                    code=row.code,
                )
                for row in rows
            )

    async def cancel(self, *, tenant_id: UUID, actor_id: UUID, run_id: UUID) -> RunView:
        async with self.session_factory.begin() as session:
            await lock_usage_tenant(session, tenant_id)
            await lock_run(session, run_id)
            run = await self._find_run(session, tenant_id, run_id)
            await self._authorized_snapshot(session, run, actor_id)
            if run.status in {"completed", "partial", "failed", "expired", "cancelled"}:
                return await self._run_view(session, run)
            run.cancel_requested = True
            units = (
                await session.scalars(
                    select(AnalysisUnit)
                    .where(AnalysisUnit.run_id == run.id)
                    .order_by(AnalysisUnit.ordinal)
                )
            ).all()
            for unit in units:
                if unit.status == "waiting_input":
                    await self.usage.release(
                        tenant_id=tenant_id,
                        operation_id=unit.id,
                        metric=ProductMetric.ANALYSIS_CELL,
                        source="analysis.cancel",
                        session=session,
                    )
                    unit.status = "cancelled"
                    request = await self._input_request(session, unit.id)
                    if request is not None and request.state == "open":
                        request.state = "cancelled"
                else:
                    await finish_unit(
                        session, run, unit, status="cancelled", code="analysis_cancelled"
                    )
            await aggregate_run(session, run)
            await append_event(session, run, "run.cancelled")
            return await self._run_view(session, run)

    async def execute(self, claim: ClaimedJob) -> None:
        run_id = UUID(str(claim.payload["run_id"]))
        async with self.session_factory() as session:
            run = await self._find_run(session, claim.tenant_id, run_id)
            if run.job_id != claim.job_id or run.actor_id != claim.actor_id:
                raise JobLeaseLost()
            requested_ids = tuple(UUID(str(value)) for value in claim.payload.get("unit_ids", ()))
            statement = select(AnalysisUnit.id).where(AnalysisUnit.run_id == run_id)
            if requested_ids:
                statement = statement.where(AnalysisUnit.id.in_(requested_ids))
            ids = tuple((await session.scalars(statement.order_by(AnalysisUnit.ordinal))).all())
        for unit_id in ids:
            await self._execute_unit(claim, run_id, unit_id)

    async def _execute_unit(self, claim: ClaimedJob, run_id: UUID, unit_id: UUID) -> None:
        try:
            async with self.session_factory.begin() as session:
                run, unit = await self._guard(session, claim, run_id, unit_id, executable=False)
                if unit.status in CLOSED_UNITS or unit.status == "waiting_input":
                    return
                snapshot = await self._authorized_snapshot(session, run, run.actor_id)
                policy = ExecutionPolicy.model_validate(run.policy)
                if (
                    self.gateway is None
                    or canonical_hash(policy) != run.policy_hash
                    or self._policy_for_mode(policy.execution_mode) != policy
                ):
                    raise AnalysisError("analysis_route_changed")
                now = datetime.now(UTC)
                if unit.deadline_at is None:
                    unit.deadline_at = min(
                        run.deadline_at, now + timedelta(seconds=policy.unit_deadline_seconds)
                    )
                if unit.deadline_at <= now:
                    raise AnalysisError("analysis_expired")
                last = await self._last_action(session, unit.id)
                if last is not None and last.state == "dispatched":
                    last.state = "unknown"
                    await finish_unit(
                        session, run, unit, status="failed", code="analysis_response_unknown"
                    )
                    return
                field = next(f for f in snapshot.payload.spec.fields if f.id == unit.field_id)
                object_name = next(
                    o.name for o in snapshot.payload.spec.objects if o.id == unit.object_id
                )
                sources = await self._load_sources(session, run, snapshot, unit)
                if unit.evidence_set is None:
                    initial_evidence = select_evidence(field, tuple(sources))
                    unit.evidence_set = initial_evidence.model_dump(mode="json")
                    unit.evidence_hash = canonical_hash(initial_evidence)
                evidence = self._evidence(unit)
                assert evidence is not None
                starting = unit.status != "running"
                unit.status = "running"
                run.status = "running"
                if starting:
                    await append_event(session, run, "unit.started", unit)

            tools_complete = await self._execute_tools(
                claim=claim,
                run_id=run_id,
                unit_id=unit_id,
                sources=tuple(sources),
                initial_evidence=evidence,
            )
            if not tools_complete:
                return
            await self._dispatch_model(
                claim=claim,
                run_id=run_id,
                unit_id=unit_id,
                field=field,
                object_name=object_name,
                initial_evidence=evidence,
                sources=tuple(sources),
                policy=policy,
            )
        except JobLeaseLost:
            raise
        except (
            AnalysisError,
            UsageError,
            TimeoutError,
            DocumentParseViolation,
            ValidationError,
        ) as error:
            code = (
                error.code
                if isinstance(error, (AnalysisError, UsageError))
                else "analysis_response_unknown"
                if isinstance(error, TimeoutError)
                else "analysis_source_invalid"
            )
            async with self.session_factory.begin() as session:
                run, unit = await self._guard(session, claim, run_id, unit_id, executable=False)
                if unit.status not in CLOSED_UNITS and unit.status != "waiting_input":
                    action = await self._last_action(session, unit_id)
                    if action and action.state == "dispatched":
                        action.state = (
                            "unknown" if code == "analysis_response_unknown" else "failed"
                        )
                        action.error_code = code
                    await finish_unit(
                        session,
                        run,
                        unit,
                        status="expired" if code == "analysis_expired" else "failed",
                        code=code,
                    )

    async def _dispatch_model(
        self,
        *,
        claim: ClaimedJob,
        run_id: UUID,
        unit_id: UUID,
        field: AnalysisField,
        object_name: str,
        initial_evidence: EvidenceSet,
        sources: FrozenStructures,
        policy: ExecutionPolicy,
    ) -> None:
        while True:
            async with self.session_factory.begin() as session:
                run, unit = await self._guard(session, claim, run_id, unit_id)
                actions = await self._actions(session, unit_id)
                tool_rows = await self._tool_rows(session, unit_id)
                last = actions[-1] if actions else None
                if last is not None and last.state not in {"retryable", "completed"}:
                    raise AnalysisError("analysis_response_unknown")
                count = len(actions)
                if count >= min(2, self.calls.settings.agent_call_limit):
                    raise AnalysisError("analysis_dispatch_limit")
                delay = (
                    max(0.0, (last.retry_at - datetime.now(UTC)).total_seconds())
                    if last is not None and last.state == "retryable" and last.retry_at
                    else 0.0
                )
                assert unit.deadline_at is not None
                remaining = (unit.deadline_at - datetime.now(UTC)).total_seconds()
                if delay >= remaining:
                    raise AnalysisError("analysis_expired")
                input_row = await self._input_request(session, unit_id)
                snapshot = await self._authorized_snapshot(session, run, run.actor_id)
                frozen_inputs = {
                    parameter.id: parameter.value
                    for parameter in snapshot.payload.spec.parameters
                    if parameter.id in field.parameter_ids
                }
                user_inputs = dict(input_row.values or {}) if input_row else {}
                user_inputs.update(frozen_inputs)
                tool_results = tuple(dict(row.result or {}) for row in tool_rows)
                allow_tools = policy.execution_mode == "agent" and count == 0 and not tool_rows
                request_fingerprint = self._payload_hash(
                    {
                        "policy": run.policy_hash,
                        "source": run.source_manifest_hash,
                        "evidence": unit.evidence_hash,
                        "field": unit.field_id,
                        "slot": count + 1,
                        "allow_tools": allow_tools,
                        "tool_results": tool_results,
                        "user_inputs": user_inputs,
                    }
                )
            if delay:
                await asyncio.sleep(delay)

            async def guard(session: AsyncSession, expected_count: int = count) -> None:
                await self._guard(session, claim, run_id, unit_id)
                current = await self._actions(session, unit_id)
                if len(current) != expected_count or (
                    current and current[-1].state not in {"retryable", "completed"}
                ):
                    raise AnalysisError("analysis_response_unknown")

            async def link(
                session: AsyncSession,
                dispatch: ProviderDispatch,
                expected_count: int = count,
                fingerprint: str = request_fingerprint,
            ) -> None:
                slot = await self._dispatch_count(session, unit_id) + 1
                if slot != expected_count + 1 or slot > 2:
                    raise AnalysisError("analysis_dispatch_limit")
                session.add(
                    AnalysisAction(
                        tenant_id=claim.tenant_id,
                        unit_id=unit_id,
                        slot=slot,
                        provider_dispatch_id=dispatch.id,
                        request_fingerprint=fingerprint,
                    )
                )

            try:
                assert self.gateway is not None
                async with asyncio.timeout(remaining - delay):
                    with self.calls.scope(
                        tenant_id=claim.tenant_id,
                        operation_id=unit_id,
                        kind="agent",
                        guard=guard,
                        intent_link=link,
                    ):
                        decision = await self.gateway.generate(
                            field=field,
                            evidence=initial_evidence,
                            object_name=object_name,
                            allow_tools=allow_tools,
                            tool_results=tool_results,
                            user_inputs=user_inputs,
                        )
                if isinstance(decision, ToolDecision):
                    await self._persist_tool_decision(
                        claim=claim,
                        run_id=run_id,
                        unit_id=unit_id,
                        decision=decision,
                    )
                    complete = await self._execute_tools(
                        claim=claim,
                        run_id=run_id,
                        unit_id=unit_id,
                        sources=sources,
                        initial_evidence=initial_evidence,
                    )
                    if not complete:
                        return
                    continue

                evidence_windows, calculations = await self._decision_context(
                    unit_id=unit_id, initial_evidence=initial_evidence
                )
                result = validate_candidate(
                    decision,
                    field,
                    evidence_windows,
                    calculations=calculations,
                )
                async with self.session_factory.begin() as session:
                    run, unit = await self._guard(session, claim, run_id, unit_id)
                    action = await self._last_action(session, unit_id)
                    if action is None or action.state != "dispatched":
                        raise AnalysisError("analysis_response_unknown")
                    action.state = "completed"
                    if result.action == "need_input":
                        await self._ensure_input_request(
                            session,
                            run,
                            unit,
                            tuple(InputParameter(name=name) for name in result.missing_parameters),
                        )
                    await finish_unit(
                        session,
                        run,
                        unit,
                        status="waiting_input" if result.action == "need_input" else "succeeded",
                        result=result,
                    )
                return
            except ModelFailure as error:
                async with self.session_factory.begin() as session:
                    run, unit = await self._guard(session, claim, run_id, unit_id)
                    action = await self._last_action(session, unit_id)
                    can_retry = (
                        action is not None
                        and error.retryable
                        and action.slot < min(2, self.calls.settings.agent_call_limit)
                    )
                    if action:
                        action.state = (
                            "retryable" if can_retry else "unknown" if error.unknown else "failed"
                        )
                        action.error_code = error.code
                        action.retry_at = (
                            datetime.now(UTC) + timedelta(seconds=error.delay)
                            if can_retry
                            else None
                        )
                    if not can_retry:
                        await finish_unit(session, run, unit, status="failed", code=error.code)
                        return

    def _policy_for_mode(self, mode: ExecutionMode) -> ExecutionPolicy | None:
        if self.gateway is None:
            return None
        policy = self.gateway.policy
        if mode == "fixed":
            return policy
        return policy.model_copy(
            update={
                "schema_version": "analysis.execution.v2",
                "prompt_version": "analysis.agent.v1",
                "execution_mode": "agent",
                "max_tool_rounds": 1,
            }
        )

    async def _load_sources(
        self,
        session: AsyncSession,
        run: AnalysisRun,
        snapshot: SnapshotView,
        unit: AnalysisUnit,
    ) -> list[tuple[FrozenSource, ParsedDocumentStructure]]:
        sources: list[tuple[FrozenSource, ParsedDocumentStructure]] = []
        for source in snapshot.payload.sources:
            if source.object_id != unit.object_id:
                continue
            parsed = await load_structure(
                session,
                tenant_id=run.tenant_id,
                document_version_id=source.document_version_id,
                generation_id=source.generation_id,
                source_sha256=source.source_sha256,
            )
            if structure_digest(parsed) != source.structure_sha256:
                raise AnalysisError("analysis_source_invalid")
            sources.append((source, parsed))
        return sources

    async def _persist_tool_decision(
        self,
        *,
        claim: ClaimedJob,
        run_id: UUID,
        unit_id: UUID,
        decision: ToolDecision,
    ) -> None:
        async with self.session_factory.begin() as session:
            run, unit = await self._guard(session, claim, run_id, unit_id)
            action = await self._last_action(session, unit_id)
            if action is None or action.state != "dispatched" or action.slot != 1:
                raise AnalysisError("analysis_terminal_action_required")
            if await self._tool_rows(session, unit_id):
                raise AnalysisError("analysis_response_unknown")
            action.state = "completed"
            for ordinal, request in enumerate(decision.tools, 1):
                payload = request.model_dump(mode="json")
                session.add(
                    AnalysisToolExecution(
                        tenant_id=run.tenant_id,
                        unit_id=unit.id,
                        ordinal=ordinal,
                        tool_name=request.tool,
                        request=payload,
                        request_fingerprint=self._payload_hash(payload),
                    )
                )
            await append_event(session, run, "tools.requested", unit)

    async def _execute_tools(
        self,
        *,
        claim: ClaimedJob,
        run_id: UUID,
        unit_id: UUID,
        sources: FrozenStructures,
        initial_evidence: EvidenceSet,
    ) -> bool:
        async with self.session_factory.begin() as session:
            run, unit = await self._guard(session, claim, run_id, unit_id)
            rows = await self._tool_rows(session, unit_id)
            if not rows:
                return True
            request = await self._input_request(session, unit_id)
            inputs = dict(request.values or {}) if request else {}
            snapshot = await self._authorized_snapshot(session, run, run.actor_id)
            field = next(item for item in snapshot.payload.spec.fields if item.id == unit.field_id)
            inputs.update(
                {
                    parameter.id: parameter.value
                    for parameter in snapshot.payload.spec.parameters
                    if parameter.id in field.parameter_ids
                }
            )
            evidence_ids = {window.id for window in initial_evidence.windows}
            for row in rows:
                if row.request_fingerprint != self._payload_hash(row.request):
                    raise AnalysisError("analysis_tool_receipt_invalid")
                if row.state == "completed":
                    if row.result is None or row.result_hash != self._payload_hash(row.result):
                        raise AnalysisError("analysis_tool_receipt_invalid")
                    evidence_ids.update(self._result_evidence_ids(row.result))
                    continue
                if row.state == "failed":
                    raise AnalysisError(row.error_code or "analysis_tool_failed")
                try:
                    result = self._run_tool(
                        row=row,
                        sources=sources,
                        evidence_ids=evidence_ids,
                        inputs=inputs,
                    )
                except ToolInputRequired as missing:
                    typed = CalculateRequest.model_validate(row.request)
                    units = {
                        operand.parameter: operand.unit
                        for operand in typed.operands
                        if operand.parameter is not None
                    }
                    parameters = tuple(
                        InputParameter(name=name, unit=units.get(name))
                        for name in missing.parameters
                    )
                    row.state = "waiting_input"
                    await self._ensure_input_request(session, run, unit, parameters)
                    waiting = validate_candidate(
                        Candidate(
                            action="need_input",
                            evidence_state="insufficient",
                            reason="The calculation requires explicit user parameters.",
                            missing_parameters=missing.parameters,
                        ),
                        next(
                            field
                            for field in (
                                await self._authorized_snapshot(session, run, run.actor_id)
                            ).payload.spec.fields
                            if field.id == unit.field_id
                        ),
                        initial_evidence.windows,
                    )
                    await finish_unit(session, run, unit, status="waiting_input", result=waiting)
                    await append_event(session, run, "input.required", unit)
                    return False
                except ToolExecutionError as error:
                    row.state = "failed"
                    row.error_code = error.code
                    await finish_unit(session, run, unit, status="failed", code=error.code)
                    return False
                row.result = result
                row.result_hash = self._payload_hash(result)
                row.state = "completed"
                evidence_ids.update(self._result_evidence_ids(result))
                await append_event(session, run, "tool.completed", unit)
            return True

    def _run_tool(
        self,
        *,
        row: AnalysisToolExecution,
        sources: FrozenStructures,
        evidence_ids: set[str],
        inputs: dict[str, str],
    ) -> dict[str, object]:
        prefix = f"t{row.ordinal}"
        if row.tool_name == "search_sources":
            evidence = search_sources(
                SearchSourcesRequest.model_validate(row.request),
                sources,
                evidence_prefix=prefix,
            )
            return {"tool": row.tool_name, "evidence": evidence.model_dump(mode="json")}
        if row.tool_name == "read_source":
            evidence = read_source(
                ReadSourceRequest.model_validate(row.request),
                sources,
                evidence_prefix=prefix,
            )
            return {"tool": row.tool_name, "evidence": evidence.model_dump(mode="json")}
        if row.tool_name == "calculate":
            receipt = calculate(
                CalculateRequest.model_validate(row.request),
                evidence_ids=evidence_ids,
                inputs=inputs,
                calculation_id=f"c{row.ordinal}",
            )
            return {"tool": row.tool_name, "calculation": receipt.model_dump(mode="json")}
        raise AnalysisError("analysis_tool_invalid")

    async def _ensure_input_request(
        self,
        session: AsyncSession,
        run: AnalysisRun,
        unit: AnalysisUnit,
        parameters: tuple[InputParameter, ...],
    ) -> AnalysisInputRequest:
        existing = await self._input_request(session, unit.id)
        payload = [item.model_dump(mode="json") for item in parameters]
        if existing is not None and existing.state == "open":
            if existing.parameters != payload:
                raise AnalysisError("analysis_input_revision_conflict")
            return existing
        revision = (existing.revision if existing is not None else 0) + 1
        value = AnalysisInputRequest(
            tenant_id=run.tenant_id,
            unit_id=unit.id,
            revision=revision,
            parameters=payload,
        )
        session.add(value)
        await session.flush()
        return value

    async def _decision_context(
        self, *, unit_id: UUID, initial_evidence: EvidenceSet
    ) -> tuple[tuple[EvidenceWindow, ...], tuple[CalculationReceipt, ...]]:
        windows = list(initial_evidence.windows)
        calculations = []
        async with self.session_factory() as session:
            for row in await self._tool_rows(session, unit_id):
                if row.state != "completed" or row.result is None:
                    raise AnalysisError("analysis_tool_receipt_invalid")
                if row.result_hash != self._payload_hash(row.result):
                    raise AnalysisError("analysis_tool_receipt_invalid")
                if "evidence" in row.result:
                    evidence = EvidenceSet.model_validate(row.result["evidence"])
                    windows.extend(evidence.windows)
                if "calculation" in row.result:
                    calculations.append(
                        CalculationReceipt.model_validate(row.result["calculation"])
                    )
        return tuple(windows), tuple(calculations)

    @staticmethod
    def _result_evidence_ids(result: dict[str, object]) -> set[str]:
        if "evidence" not in result:
            return set()
        return {item.id for item in EvidenceSet.model_validate(result["evidence"]).windows}

    @staticmethod
    async def _tool_rows(session: AsyncSession, unit_id: UUID) -> tuple[AnalysisToolExecution, ...]:
        return tuple(
            (
                await session.scalars(
                    select(AnalysisToolExecution)
                    .where(AnalysisToolExecution.unit_id == unit_id)
                    .order_by(AnalysisToolExecution.ordinal)
                )
            ).all()
        )

    @staticmethod
    async def _actions(session: AsyncSession, unit_id: UUID) -> tuple[AnalysisAction, ...]:
        return tuple(
            (
                await session.scalars(
                    select(AnalysisAction)
                    .where(AnalysisAction.unit_id == unit_id)
                    .order_by(AnalysisAction.slot)
                )
            ).all()
        )

    async def _guard(
        self,
        session: AsyncSession,
        claim: ClaimedJob,
        run_id: UUID,
        unit_id: UUID,
        *,
        executable: bool = True,
    ) -> tuple[AnalysisRun, AnalysisUnit]:
        # Consistent order: Tenant -> run advisory -> Job -> business/quota -> provider budgets.
        await lock_usage_tenant(session, claim.tenant_id)
        await lock_run(session, run_id)
        job = await session.scalar(
            select(Job)
            .where(Job.id == claim.job_id, Job.tenant_id == claim.tenant_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            job is None
            or job.status != "running"
            or job.lease_token != claim.lease_token
            or job.fencing_token != claim.fencing_token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
            or job.cancel_requested_at is not None
        ):
            raise JobLeaseLost()
        run = await self._find_run(session, claim.tenant_id, run_id)
        unit = await session.scalar(
            select(AnalysisUnit).where(
                AnalysisUnit.id == unit_id,
                AnalysisUnit.run_id == run_id,
                AnalysisUnit.tenant_id == claim.tenant_id,
            )
        )
        if unit is None or run.job_id != claim.job_id or run.actor_id != claim.actor_id:
            raise JobLeaseLost()
        if executable:
            if (
                run.cancel_requested
                or unit.status in CLOSED_UNITS
                or unit.status == "waiting_input"
            ):
                raise AnalysisError("analysis_cancelled")
            if run.deadline_at <= now or (unit.deadline_at is not None and unit.deadline_at <= now):
                raise AnalysisError("analysis_expired")
            await self._authorized_snapshot(session, run, run.actor_id)
            await self.usage.require_reserved(
                tenant_id=run.tenant_id,
                operation_id=unit.id,
                metric=ProductMetric.ANALYSIS_CELL,
                session=session,
            )
        return run, unit

    async def _authorized_snapshot(
        self, session: AsyncSession, run: AnalysisRun, actor_id: UUID
    ) -> SnapshotView:
        await self._project(session, run.tenant_id, actor_id, run.project_id)
        row = await session.scalar(
            select(AnalysisSourceSnapshot).where(
                AnalysisSourceSnapshot.id == run.snapshot_id,
                AnalysisSourceSnapshot.tenant_id == run.tenant_id,
                AnalysisSourceSnapshot.project_id == run.project_id,
            )
        )
        if row is None or row.source_manifest_hash != run.source_manifest_hash:
            raise AnalysisError("analysis_source_invalid")
        return await self._snapshot_view(session, row, actor_id)

    @staticmethod
    async def _find_run(session: AsyncSession, tenant_id: UUID, run_id: UUID) -> AnalysisRun:
        row = await session.scalar(
            select(AnalysisRun).where(AnalysisRun.tenant_id == tenant_id, AnalysisRun.id == run_id)
        )
        if row is None:
            raise AnalysisError("analysis_not_found")
        return row

    @staticmethod
    async def _dispatch_count(session: AsyncSession, unit_id: UUID) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AnalysisAction)
                .where(AnalysisAction.unit_id == unit_id)
            )
            or 0
        )

    @staticmethod
    async def _last_action(session: AsyncSession, unit_id: UUID) -> AnalysisAction | None:
        return cast(
            AnalysisAction | None,
            await session.scalar(
                select(AnalysisAction)
                .where(AnalysisAction.unit_id == unit_id)
                .order_by(AnalysisAction.slot.desc())
                .limit(1)
            ),
        )

    @staticmethod
    async def _input_request(session: AsyncSession, unit_id: UUID) -> AnalysisInputRequest | None:
        return cast(
            AnalysisInputRequest | None,
            await session.scalar(
                select(AnalysisInputRequest)
                .where(AnalysisInputRequest.unit_id == unit_id)
                .order_by(AnalysisInputRequest.revision.desc())
                .limit(1)
            ),
        )

    @staticmethod
    def _input_view(
        request: AnalysisInputRequest,
        run: AnalysisRun,
        unit: AnalysisUnit,
        *,
        has_dispatch_slot: bool,
    ) -> InputRequestView:
        return InputRequestView(
            id=request.id,
            unit_id=unit.id,
            revision=request.revision,
            parameters=tuple(InputParameter.model_validate(item) for item in request.parameters),
            state=cast(Literal["open", "submitted", "cancelled"], request.state),
            can_resume=(
                request.state == "open"
                and unit.status == "waiting_input"
                and has_dispatch_slot
                and not run.cancel_requested
                and run.deadline_at > datetime.now(UTC)
                and unit.deadline_at is not None
                and unit.deadline_at > datetime.now(UTC)
            ),
        )

    @staticmethod
    def _review_view(review: AnalysisReview) -> ReviewView:
        return ReviewView(
            id=review.id,
            cell_revision_id=review.cell_revision_id,
            revision=review.revision,
            content_hash=review.content_hash,
            decision=cast(Literal["approved", "changes_requested", "rejected"], review.decision),
            reason=review.reason,
            actor_id=review.actor_id,
            created_at=review.created_at,
        )

    @staticmethod
    def _payload_hash(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _evidence(unit: AnalysisUnit) -> EvidenceSet | None:
        if unit.evidence_set is None:
            return None
        evidence = EvidenceSet.model_validate(unit.evidence_set)
        if canonical_hash(evidence) != unit.evidence_hash:
            raise AnalysisError("analysis_source_invalid")
        return evidence

    @staticmethod
    async def _run_view(session: AsyncSession, run: AnalysisRun) -> RunView:
        finished = await session.scalar(
            select(func.count())
            .select_from(AnalysisUnit)
            .where(AnalysisUnit.run_id == run.id, AnalysisUnit.status.in_(CLOSED_UNITS))
        )
        dispatches = await session.scalar(
            select(func.count())
            .select_from(AnalysisAction)
            .join(AnalysisUnit, AnalysisUnit.id == AnalysisAction.unit_id)
            .where(AnalysisUnit.run_id == run.id)
        )
        policy = ExecutionPolicy.model_validate(run.policy)
        return RunView(
            id=run.id,
            project_id=run.project_id,
            snapshot_id=run.snapshot_id,
            status=cast(RunStatus, run.status),
            total_units=run.total_units,
            finished_units=finished or 0,
            dispatches=dispatches or 0,
            max_dispatches=run.total_units * 2,
            execution_mode=policy.execution_mode,
            created_at=run.created_at,
            deadline_at=run.deadline_at,
            cancel_requested=run.cancel_requested,
            base_run_id=run.base_run_id,
            reused_units=(
                await session.scalar(
                    select(func.count())
                    .select_from(AnalysisUnit)
                    .where(
                        AnalysisUnit.run_id == run.id,
                        AnalysisUnit.reused_from_unit_id.is_not(None),
                    )
                )
                or 0
            ),
            affected_units=(
                run.total_units
                - (
                    await session.scalar(
                        select(func.count())
                        .select_from(AnalysisUnit)
                        .where(
                            AnalysisUnit.run_id == run.id,
                            AnalysisUnit.reused_from_unit_id.is_not(None),
                        )
                    )
                    or 0
                )
                if run.base_run_id is not None
                else 0
            ),
        )
