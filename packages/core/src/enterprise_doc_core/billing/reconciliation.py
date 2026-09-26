"""Private, bounded ledger export. Supplier billing must be reconciled separately."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.administration_contracts import (
    PlatformEntitlementOperator,
    require_entitlement_operator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.models import UsageEvent
from enterprise_doc_core.billing.product_models import (
    ProductQuota,
    ProductUsageEvent,
    ProductUsageReservation,
)
from enterprise_doc_core.billing.provider_metadata import safe_provider_id
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.billing.reconciliation_contracts import (
    BusinessEventRecord,
    LegacyPresalesRecord,
    ProviderCallRecord,
    ReconciliationSummary,
    UsageExportWindow,
    UsageReconciliationExport,
)
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall


def _bounded[T](rows: Sequence[T], limit: int) -> Sequence[T]:
    if len(rows) > limit:
        raise UsageError("reconciliation_export_limit_exceeded")
    return rows


def _total_tokens(usage: dict[str, int | None] | None) -> int | None:
    value = usage.get("total_tokens") if isinstance(usage, dict) else None
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _summarize(
    calls: Sequence[ProviderCallRecord], events: Sequence[BusinessEventRecord]
) -> ReconciliationSummary:
    possible = [call for call in calls if call.state != "not_sent"]
    quantities: dict[str, dict[str, int]] = {}
    for event in events:
        bucket = quantities.setdefault(event.metric, {"consume": 0, "release": 0})
        bucket[event.event_type] += event.quantity
    return ReconciliationSummary(
        provider_records=len(calls),
        potentially_billable_records=len(possible),
        unresolved_records=sum(
            c.state
            in {"dispatched", "running", "unknown", "timeout", "transport_error", "cancelled"}
            or c.error_code in {"presales_model_timeout", "presales_model_transport_error"}
            for c in possible
        ),
        unknown_cost_records=sum(c.estimated_cost is None or c.currency is None for c in possible),
        known_total_tokens=sum(c.total_tokens for c in possible if c.total_tokens is not None),
        missing_token_records=sum(c.total_tokens is None for c in possible),
        business_quantities=quantities,
    )


class UsageReconciliationService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = session_factory

    async def export(
        self, *, tenant_id: UUID, operator: PlatformEntitlementOperator, window: UsageExportWindow
    ) -> UsageReconciliationExport:
        require_entitlement_operator(operator)
        try:
            async with asyncio.timeout(30), self.sessions.begin() as session:
                # This precedes the first read; every ledger sees the same snapshot.
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                if await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None:
                    raise UsageError("reconciliation_tenant_not_found")
                generated_at = await session.scalar(select(func.transaction_timestamp()))
                assert generated_at is not None
                calls, call_counts = await self._calls(session, tenant_id, window)
                events, event_counts = await self._events(session, tenant_id, window)
                legacy = await self._legacy(session, tenant_id, window)
                warnings: tuple[str, ...] = (
                    "supplier_costs_not_reconciled",
                    "current_snapshot_not_historical_as_of",
                    "pre_metering_and_administrative_calls_not_covered",
                )
                if legacy:
                    warnings += ("legacy_presales_has_no_per_call_ledger",)
                return UsageReconciliationExport(
                    schema_version="usage-reconciliation.v1",
                    tenant_id=tenant_id,
                    window_start=window.start,
                    window_end_exclusive=window.end,
                    generated_at=generated_at,
                    limit_per_source=window.limit_per_source,
                    source_counts={
                        **call_counts,
                        **event_counts,
                        "legacy_presales_attempts": len(legacy),
                    },
                    provider_calls=tuple(
                        sorted(calls, key=lambda c: (c.started_at, c.ledger, c.id))
                    ),
                    business_events=tuple(
                        sorted(events, key=lambda e: (e.occurred_at, e.ledger, e.id))
                    ),
                    legacy_presales_attempts=legacy,
                    summary=_summarize(calls, events),
                    warnings=warnings,
                )
        except DBAPIError as error:
            raise UsageError("reconciliation_store_unavailable") from error

    async def _calls(
        self, session: AsyncSession, tenant_id: UUID, window: UsageExportWindow
    ) -> tuple[list[ProviderCallRecord], dict[str, int]]:
        dispatches = _bounded(
            (
                await session.scalars(
                    select(ProviderDispatch)
                    .where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.started_at >= window.start,
                        ProviderDispatch.started_at < window.end,
                    )
                    .order_by(ProviderDispatch.started_at, ProviderDispatch.id)
                    .limit(window.limit_per_source + 1)
                )
            ).all(),
            window.limit_per_source,
        )
        presales = _bounded(
            (
                await session.scalars(
                    select(PresalesProviderCall)
                    .where(
                        PresalesProviderCall.tenant_id == tenant_id,
                        PresalesProviderCall.started_at >= window.start,
                        PresalesProviderCall.started_at < window.end,
                    )
                    .order_by(PresalesProviderCall.started_at, PresalesProviderCall.id)
                    .limit(window.limit_per_source + 1)
                )
            ).all(),
            window.limit_per_source,
        )
        calls = [
            ProviderCallRecord(
                ledger="provider_dispatches",
                id=d.id,
                operation_id=d.operation_id,
                kind=d.kind,
                provider=d.provider,
                model=d.model,
                channel_key=d.channel_hash,
                route=None,
                attempt_number=None,
                state=d.state,
                error_code=None,
                http_status=d.status_code,
                provider_request_id=safe_provider_id(d.provider_request_id),
                provider_response_id=safe_provider_id(d.provider_response_id),
                total_tokens=d.total_tokens,
                estimated_cost=d.estimated_cost,
                currency=d.currency,
                started_at=d.started_at,
                finished_at=d.finished_at,
            )
            for d in dispatches
        ]
        calls.extend(
            ProviderCallRecord(
                ledger="presales_provider_calls",
                id=p.id,
                operation_id=p.operation_id,
                kind="presales",
                provider=p.model_provider,
                model=p.model_name,
                channel_key=p.route_key,
                route=p.route,
                attempt_number=p.number,
                state=p.state,
                error_code=p.error_code,
                http_status=None,
                provider_request_id=safe_provider_id(p.provider_request_id),
                provider_response_id=safe_provider_id(p.provider_response_id),
                total_tokens=_total_tokens(p.usage),
                estimated_cost=None,
                currency=None,
                started_at=p.started_at,
                finished_at=p.finished_at,
            )
            for p in presales
        )
        return calls, {
            "provider_dispatches": len(dispatches),
            "presales_provider_calls": len(presales),
        }

    async def _events(
        self, session: AsyncSession, tenant_id: UUID, window: UsageExportWindow
    ) -> tuple[list[BusinessEventRecord], dict[str, int]]:
        usage = _bounded(
            (
                await session.scalars(
                    select(UsageEvent)
                    .where(
                        UsageEvent.tenant_id == tenant_id,
                        UsageEvent.occurred_at >= window.start,
                        UsageEvent.occurred_at < window.end,
                    )
                    .order_by(UsageEvent.occurred_at, UsageEvent.id)
                    .limit(window.limit_per_source + 1)
                )
            ).all(),
            window.limit_per_source,
        )
        products = _bounded(
            (
                await session.execute(
                    select(ProductUsageEvent, ProductUsageReservation, ProductQuota.entitlement_id)
                    .join(
                        ProductUsageReservation,
                        (ProductUsageReservation.tenant_id == ProductUsageEvent.tenant_id)
                        & (ProductUsageReservation.id == ProductUsageEvent.reservation_id),
                    )
                    .join(
                        ProductQuota,
                        (ProductQuota.tenant_id == ProductUsageReservation.tenant_id)
                        & (ProductQuota.id == ProductUsageReservation.quota_id)
                        & (ProductQuota.metric == ProductUsageReservation.metric),
                    )
                    .where(
                        ProductUsageEvent.tenant_id == tenant_id,
                        ProductUsageEvent.occurred_at >= window.start,
                        ProductUsageEvent.occurred_at < window.end,
                    )
                    .order_by(ProductUsageEvent.occurred_at, ProductUsageEvent.id)
                    .limit(window.limit_per_source + 1)
                )
            ).all(),
            window.limit_per_source,
        )
        events = [
            BusinessEventRecord(
                ledger="usage_events",
                id=u.id,
                operation_id=u.operation_id,
                reservation_id=u.reservation_id,
                entitlement_id=u.entitlement_id,
                metric=u.metric,
                event_type=u.event_type,
                quantity=u.quantity,
                occurred_at=u.occurred_at,
            )
            for u in usage
        ]
        events.extend(
            BusinessEventRecord(
                ledger="product_usage_events",
                id=e.id,
                operation_id=r.operation_id,
                reservation_id=e.reservation_id,
                entitlement_id=period,
                metric=r.metric,
                event_type=e.event_type,
                quantity=r.quantity,
                occurred_at=e.occurred_at,
            )
            for e, r, period in products
        )
        return events, {"usage_events": len(usage), "product_usage_events": len(products)}

    async def _legacy(
        self, session: AsyncSession, tenant_id: UUID, window: UsageExportWindow
    ) -> tuple[LegacyPresalesRecord, ...]:
        # Synchronous attempts predate the per-call ledger. Do not invent calls or
        # duplicate a background attempt whose dispatch is outside this window.
        rows = _bounded(
            (
                await session.scalars(
                    select(PresalesAttempt)
                    .where(
                        PresalesAttempt.tenant_id == tenant_id,
                        PresalesAttempt.job_id.is_(None),
                        PresalesAttempt.created_at >= window.start,
                        PresalesAttempt.created_at < window.end,
                        ~select(PresalesProviderCall.id)
                        .where(
                            PresalesProviderCall.tenant_id == tenant_id,
                            PresalesProviderCall.operation_id == PresalesAttempt.id,
                        )
                        .exists(),
                    )
                    .order_by(PresalesAttempt.created_at, PresalesAttempt.id)
                    .limit(window.limit_per_source + 1)
                )
            ).all(),
            window.limit_per_source,
        )
        return tuple(
            LegacyPresalesRecord(
                operation_id=r.id,
                state=r.state,
                provider_request_count=r.provider_request_count,
                provider=r.model_provider,
                model=r.model_name,
                provider_response_id=safe_provider_id(r.provenance.get("providerResponseId")),
                created_at=r.created_at,
                finished_at=r.finished_at,
            )
            for r in rows
        )
