from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.demo.limits import (
    active_workspace,
    begin_background_attempt,
    finish_attempt,
)
from enterprise_doc_core.identity import Tenant
from enterprise_doc_core.jobs.models import Job
from enterprise_doc_core.jobs.service import (
    ClaimedJob,
    JobLeaseLost,
    JobRuntimeService,
    RetryDisposition,
)
from enterprise_doc_core.presales import route_health
from enterprise_doc_core.presales.access import check_sources, load_packet, load_row
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import PresalesGateway
from enterprise_doc_core.presales.generation import (
    ACTIVE_STATES,
    BACKGROUND_JOB_TYPE,
    GenerationService,
    resolve_draft,
)
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall
from enterprise_doc_core.presales.provider_calls import abandon_calls, summarize_calls
from enterprise_doc_core.presales.schemas import (
    GeneratedDraft,
    GenerationInput,
    RequirementInput,
    SavedDraft,
    SourceSnapshot,
)


class BackgroundGeneration:
    """Bounded PostgreSQL polling in the existing worker; no Celery retry chain."""

    def __init__(
        self,
        generation: GenerationService,
        gateways: Mapping[str, PresalesGateway],
        *,
        lease_seconds: float = 30,
    ) -> None:
        self.generation = generation
        self.sessions, self.clock, self.settings = (
            generation.session_factory,
            generation.clock,
            generation.settings,
        )
        self.gateways = dict(gateways)
        self.runtime = JobRuntimeService(
            session_factory=self.sessions,
            clock=self.clock,
            lease_seconds=lease_seconds,
            failure_projector=self.project_failure,
        )

    async def run_once(self, worker_id: str) -> bool:
        if await self._reconcile_terminal():
            return True
        now = self.clock()
        async with self.sessions() as session:
            ids = (
                await session.scalars(
                    select(Job.id)
                    .where(
                        Job.type == BACKGROUND_JOB_TYPE,
                        or_(
                            and_(Job.status == "pending", Job.available_at <= now),
                            and_(Job.status == "running", Job.lease_expires_at <= now),
                        ),
                    )
                    .order_by(Job.available_at, Job.id)
                    .limit(32)
                )
            ).all()
        for job_id in ids:
            claim = await self.runtime.claim(job_id=job_id, worker_id=worker_id)
            if claim is None:
                continue
            await self._supervise(claim)
            return True
        return False

    async def _supervise(self, claim: ClaimedJob) -> None:
        async def pulse() -> None:
            while True:
                await asyncio.sleep(self.runtime.lease_seconds / 3)
                if await self.runtime.heartbeat(claim):
                    raise PresalesError("presales_generation_cancelled")

        work, heartbeat = asyncio.create_task(self.execute(claim)), asyncio.create_task(pulse())
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
            else:
                await heartbeat
        except JobLeaseLost:
            pass
        except PresalesError as error:
            await self.runtime.fail(
                claim,
                disposition=RetryDisposition.PERMANENT,
                error_code=error.code,
                error_message="Generation cancelled.",
            )
        finally:
            # Shutdown interrupts local I/O only. The lease and dispatch slot survive.
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    @staticmethod
    def principal(claim: ClaimedJob) -> PrincipalContext:
        return PrincipalContext(str(claim.tenant_id), str(claim.actor_id), "member")

    async def _lease(self, session: AsyncSession, claim: ClaimedJob) -> Job:
        job = await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
        if (
            job is None
            or job.type != BACKGROUND_JOB_TYPE
            or job.tenant_id != claim.tenant_id
            or job.actor_id != claim.actor_id
            or job.status != "running"
            or job.lease_token != claim.lease_token
            or job.fencing_token != claim.fencing_token
            or job.locked_by != claim.worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= self.clock()
        ):
            raise JobLeaseLost()
        if job.cancel_requested_at is not None:
            raise PresalesError("presales_generation_cancelled")
        return job

    async def _operation(self, session: AsyncSession, claim: ClaimedJob) -> PresalesAttempt:
        operation = await session.scalar(
            select(PresalesAttempt)
            .where(
                PresalesAttempt.id == UUID(claim.payload["operation_id"]),
                PresalesAttempt.tenant_id == claim.tenant_id,
                PresalesAttempt.job_id == claim.job_id,
                PresalesAttempt.row_id == UUID(claim.payload["row_id"]),
            )
            .with_for_update()
        )
        if operation is None:
            raise JobLeaseLost()
        return operation

    async def _calls(
        self, session: AsyncSession, operation: PresalesAttempt
    ) -> list[PresalesProviderCall]:
        return list(
            (
                await session.scalars(
                    select(PresalesProviderCall)
                    .where(
                        PresalesProviderCall.operation_id == operation.id,
                        PresalesProviderCall.tenant_id == operation.tenant_id,
                    )
                    .order_by(PresalesProviderCall.number)
                )
            ).all()
        )

    def _active(self, operation: PresalesAttempt) -> None:
        if operation.state not in ACTIVE_STATES or operation.deadline_at <= self.clock():
            raise PresalesError("presales_attempt_expired")

    async def _start(
        self, claim: ClaimedJob
    ) -> tuple[RequirementInput, list[SourceSnapshot], datetime] | None:
        async with self.sessions.begin() as session:
            await self._lease(session, claim)
            packet = await load_packet(
                session, self.principal(claim), UUID(claim.payload["packet_id"]), lock=True
            )
            await active_workspace(session, packet.tenant_id, self.clock())
            row = await load_row(session, packet, UUID(claim.payload["row_id"]))
            operation = await self._operation(session, claim)
            if operation.state == "succeeded":
                return None
            self._active(operation)
            if operation.started_at is None:
                operation.started_at = self.clock()
                operation.deadline_at = operation.started_at + timedelta(
                    seconds=self.settings.row_timeout_seconds
                )
            calls = await self._calls(session, operation)
            abandon_calls(calls, self.clock())
            summarize_calls(operation, calls)
            operation.state = "recovering" if calls else "running"
            return (
                RequirementInput(
                    key=row.requirement_key,
                    text=row.requirement_text,
                    source_location=row.source_location,
                ),
                await check_sources(session, packet),
                operation.deadline_at,
            )

    async def execute(self, claim: ClaimedJob) -> None:
        try:
            started = await self._start(claim)
            if started is not None:
                requirement, sources, deadline = started
                async with asyncio.timeout(max(0, (deadline - self.clock()).total_seconds())):
                    candidates, notes = await self.generation._retrieve(
                        claim.tenant_id, claim.actor_id, requirement, sources
                    )
                    payload = GenerationInput(
                        requirement=requirement,
                        sources=sources,
                        evidence=[
                            {
                                "chunkId": str(c.chunk_id),
                                "documentVersionId": str(c.document_version_id),
                                "text": c.text,
                                "filename": c.source_filename or "",
                                "heading": c.heading or "",
                                "pageNumber": str(c.page_number or ""),
                            }
                            for c in candidates
                        ],
                    )
                    excluded: set[str] = set()
                    while True:
                        route = await self._next_route(claim, excluded)
                        gateway = self.gateways[route]
                        remaining = (deadline - self.clock()).total_seconds() - 2
                        if remaining <= 0:
                            raise PresalesError("presales_attempt_expired")
                        try:
                            call_id = await self._dispatch(claim, route, gateway)
                        except PresalesError as error:
                            if error.code != "presales_route_cooling":
                                raise
                            excluded.add(route)
                            continue
                        try:
                            async with asyncio.timeout(remaining):
                                generated = await gateway.generate(payload)
                            try:
                                saved = resolve_draft(
                                    generated.draft, candidates, sources, claim.tenant_id, notes
                                )
                            except PresalesError as error:
                                error.usage, error.provider_response_id = (
                                    generated.usage,
                                    generated.provider_response_id,
                                )
                                raise
                        except TimeoutError:
                            await self._record_error(
                                claim,
                                call_id,
                                PresalesError(
                                    "presales_model_timeout", provider_requests=1, retryable=True
                                ),
                            )
                            continue
                        except PresalesError as error:
                            await self._record_error(claim, call_id, error)
                            if not error.retryable:
                                raise
                            continue
                        try:
                            await self._save(claim, call_id, gateway, generated, saved)
                        except JobLeaseLost:
                            raise
                        except Exception as error:
                            # Delivery can be refused after HTTP has completed (ACL,
                            # deadline or quota change). Keep known usage under the
                            # current fence even though no draft is published.
                            failure = PresalesError(
                                error.code
                                if isinstance(error, PresalesError)
                                else "presales_delivery_failed",
                                provider_requests=1,
                                usage=generated.usage,
                                provider_response_id=generated.provider_response_id,
                            )
                            await self._record_error(claim, call_id, failure)
                            raise failure from error
                        break
            await self.runtime.succeed(claim)
        except asyncio.CancelledError:
            raise
        except JobLeaseLost:
            raise
        except Exception as error:
            code = (
                error.code
                if isinstance(error, PresalesError)
                else "presales_generation_timeout"
                if isinstance(error, TimeoutError)
                else "presales_generation_failed"
            )
            await self.runtime.fail(
                claim,
                disposition=RetryDisposition.PERMANENT,
                error_code=code,
                error_message="Generation could not complete.",
            )

    def _route_key(self, route: str) -> str:
        gateway = self.gateways[route]
        endpoint = getattr(getattr(gateway, "settings", None), "base_url", route)
        return hashlib.sha256(f"{endpoint}:{gateway.model_name}".encode()).hexdigest()

    async def _next_route(self, claim: ClaimedJob, excluded: set[str]) -> str:
        async with self.sessions.begin() as session:
            await self._lease(session, claim)
            operation = await self._operation(session, claim)
            self._active(operation)
            calls = await self._calls(session, operation)
            maximum = 2 if self.settings.automatic_failover_enabled else 1
            if calls and (len(calls) >= maximum or not calls[-1].retryable):
                raise PresalesError(calls[-1].error_code or "presales_dispatch_limit")
            order = [self.settings.model_route]
            if maximum == 2:
                order.append("fallback" if order[0] == "primary" else "primary")
            for route in order:
                if (
                    route not in excluded
                    and route in self.gateways
                    and all(
                        c.route != route and c.route_key != self._route_key(route) for c in calls
                    )
                    and await route_health.available(session, self._route_key(route), self.clock())
                ):
                    return route
            raise PresalesError("presales_model_unavailable")

    async def _dispatch(self, claim: ClaimedJob, route: str, gateway: PresalesGateway) -> UUID:
        async with self.sessions.begin() as session:
            await self._lease(session, claim)
            packet = await load_packet(
                session, self.principal(claim), UUID(claim.payload["packet_id"]), lock=True
            )
            await active_workspace(session, packet.tenant_id, self.clock())
            operation = await self._operation(session, claim)
            self._active(operation)
            calls = await self._calls(session, operation)
            if len(calls) >= (2 if self.settings.automatic_failover_enabled else 1) or any(
                c.route == route or c.route_key == self._route_key(route) for c in calls
            ):
                raise PresalesError("presales_dispatch_limit")
            await begin_background_attempt(
                session, claim.tenant_id, operation.id, self.clock(), operation.deadline_at
            )
            call = PresalesProviderCall(
                id=uuid4(),
                tenant_id=claim.tenant_id,
                operation_id=operation.id,
                number=len(calls) + 1,
                route=route,
                route_key=self._route_key(route),
                fencing_token=claim.fencing_token,
                state="running",
                model_provider=gateway.model_provider,
                model_name=gateway.model_name,
                started_at=self.clock(),
            )
            await route_health.admit(
                session,
                call,
                now=self.clock(),
                deadline=operation.deadline_at,
                settings=self.settings,
            )
            session.add(call)
            operation.provider_request_count = None
            operation.state = "recovering" if calls else "running"
            return call.id

    async def _record_error(self, claim: ClaimedJob, call_id: UUID, error: PresalesError) -> None:
        async with self.sessions.begin() as session:
            await self._lease(session, claim)
            operation = await self._operation(session, claim)
            calls = await self._calls(session, operation)
            call = next(c for c in calls if c.id == call_id)
            call.state, call.finished_at = (
                "failed" if error.provider_requests else "not_sent",
                self.clock(),
            )
            call.error_code, call.retryable = error.code, error.retryable
            call.usage, call.provider_response_id = error.usage, error.provider_response_id
            await route_health.observed(session, call, now=self.clock(), settings=self.settings)
            operation.state = "recovering" if error.retryable else "running"
            summarize_calls(operation, calls)

    async def _save(
        self,
        claim: ClaimedJob,
        call_id: UUID,
        gateway: PresalesGateway,
        generated: GeneratedDraft,
        saved: SavedDraft,
    ) -> None:
        async with self.sessions.begin() as session:
            job = await self._lease(session, claim)
            packet = await load_packet(
                session, self.principal(claim), UUID(claim.payload["packet_id"]), lock=True
            )
            await active_workspace(session, packet.tenant_id, self.clock())
            row = await load_row(session, packet, UUID(claim.payload["row_id"]))
            operation = await self._operation(session, claim)
            self._active(operation)
            if row.draft is not None:
                raise PresalesError("presales_attempt_expired")
            calls = await self._calls(session, operation)
            call = next(c for c in calls if c.id == call_id)
            if call.state != "running" or call.fencing_token != claim.fencing_token:
                raise JobLeaseLost()
            call.state, call.finished_at = "succeeded", self.clock()
            call.usage, call.provider_response_id = generated.usage, generated.provider_response_id
            call.model_name = generated.returned_model or gateway.model_name
            await route_health.observed(session, call, now=self.clock(), settings=self.settings)
            summarize_calls(operation, calls)
            if self.generation.usage_service is not None:
                await self.generation.usage_service.settle_provider_request(
                    tenant_id=claim.tenant_id,
                    operation_id=operation.id,
                    provider=gateway.model_provider,
                    model=call.model_name,
                    usage=operation.usage,
                    source="presales",
                    session=session,
                )
            operation.state, operation.finished_at = "succeeded", self.clock()
            operation.model_provider, operation.model_name = (
                gateway.model_provider,
                gateway.model_name,
            )
            operation.provenance = {
                **operation.provenance,
                **gateway.provenance,
                "returnedModel": generated.returned_model,
                "providerResponseId": generated.provider_response_id,
            }
            await finish_attempt(session, claim.tenant_id, operation.id)
            row.draft, row.revision = saved.model_dump(mode="json"), 1
            await append_audit_event(
                session,
                tenant_id=claim.tenant_id,
                actor_id=claim.actor_id,
                action="presales.row.generated",
                resource_type="presales_packet",
                resource_id=packet.id,
                request_id=job.request_id,
                correlation_id=job.correlation_id,
                metadata={
                    "row_id": str(row.id),
                    "attempt_id": str(operation.id),
                    "status": saved.status,
                },
            )

    async def project_failure(
        self,
        session: AsyncSession,
        *,
        claim: ClaimedJob,
        status: str,
        error_code: str,
        error_message: str,
        failure_metadata: Mapping[str, Any] | None,
        now: datetime,
    ) -> None:
        await session.scalar(
            select(Tenant.id).where(Tenant.id == claim.tenant_id).with_for_update()
        )
        operation = await self._operation(session, claim)
        await self._fail_operation(session, operation, error_code, now)

    async def _reconcile_terminal(self) -> bool:
        async with self.sessions.begin() as session:
            job = await session.scalar(
                select(Job)
                .where(
                    Job.type == BACKGROUND_JOB_TYPE,
                    Job.status.in_(("cancelled", "dead")),
                    select(PresalesAttempt.id)
                    .where(
                        PresalesAttempt.job_id == Job.id, PresalesAttempt.state.in_(ACTIVE_STATES)
                    )
                    .exists(),
                )
                .order_by(Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            await session.scalar(
                select(Tenant.id).where(Tenant.id == job.tenant_id).with_for_update()
            )
            operation = await session.scalar(
                select(PresalesAttempt)
                .where(
                    PresalesAttempt.job_id == job.id,
                    PresalesAttempt.tenant_id == job.tenant_id,
                )
                .with_for_update()
            )
            if operation is not None:
                await self._fail_operation(
                    session,
                    operation,
                    job.last_error_code or "presales_generation_cancelled",
                    self.clock(),
                )
            return True

    async def _fail_operation(
        self, session: AsyncSession, operation: PresalesAttempt, error_code: str, now: datetime
    ) -> None:
        if operation.state not in ACTIVE_STATES:
            return
        calls = await self._calls(session, operation)
        abandon_calls(calls, now)
        summarize_calls(operation, calls)
        operation.state = "expired" if operation.deadline_at <= now else "failed"
        operation.error_code, operation.finished_at = error_code, now
        await finish_attempt(session, operation.tenant_id, operation.id)
        if self.generation.usage_service is not None:
            await self.generation.usage_service.release_provider_request(
                tenant_id=operation.tenant_id,
                operation_id=operation.id,
                source="presales",
                session=session,
            )
