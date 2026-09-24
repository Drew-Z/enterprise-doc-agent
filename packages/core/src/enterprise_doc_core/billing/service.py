from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.contracts import (
    ProviderUsageSummary,
    ReservationResult,
    TenantResourceUsage,
    UsageEventView,
    UsageSummary,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.billing.product_contracts import ProductMetric, ProductQuotaView
from enterprise_doc_core.billing.product_models import ProductQuota
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.identity.seats import membership_seats


class EntitlementUsageService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
        reservation_ttl_seconds: int = 900,
        app_env: AppEnvironment = AppEnvironment.LOCAL,
    ) -> None:
        if reservation_ttl_seconds <= 0:
            raise ValueError("reservation_ttl_seconds must be positive")
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.reservation_ttl = timedelta(seconds=reservation_ttl_seconds)
        self.require_active_entitlement = app_env in {
            AppEnvironment.STAGING,
            AppEnvironment.PRODUCTION,
        }

    async def reserve_provider_request(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        quantity: int = 1,
        source: str = "unknown",
        session: AsyncSession | None = None,
    ) -> ReservationResult:
        if quantity <= 0:
            raise UsageError("usage_invalid_quantity")
        async with self._transaction(session) as transaction:
            return await self._reserve(
                transaction,
                tenant_id=tenant_id,
                operation_id=operation_id,
                quantity=quantity,
                source=source,
            )

    async def settle_provider_request(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, Any] | None = None,
        currency: str | None = None,
        estimated_cost: Decimal | int | float | str | None = None,
        pricing_version: str | None = None,
        source: str = "unknown",
        session: AsyncSession | None = None,
    ) -> ReservationResult:
        try:
            async with self._transaction(session) as transaction:
                return await self._settle(
                    transaction,
                    tenant_id=tenant_id,
                    operation_id=operation_id,
                    provider=provider,
                    model=model,
                    usage=usage,
                    currency=currency,
                    estimated_cost=estimated_cost,
                    pricing_version=pricing_version,
                    source=source,
                )
        except UsageError as error:
            # `_settle` deliberately raises for an expired reservation.  The
            # surrounding transaction is rolled back by the context manager,
            # so release it in a short follow-up transaction before surfacing
            # the stable expiry error to the caller.
            if session is None and error.code == "usage_reservation_expired":
                await self.release_provider_request(
                    tenant_id=tenant_id, operation_id=operation_id, source="expiry"
                )
            raise

    async def release_provider_request(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        source: str = "unknown",
        session: AsyncSession | None = None,
    ) -> ReservationResult:
        async with self._transaction(session) as transaction:
            return await self._release(
                transaction, tenant_id=tenant_id, operation_id=operation_id, source=source
            )

    async def summary(
        self, *, tenant_id: UUID, now: datetime | None = None, recent_limit: int = 20
    ) -> UsageSummary:
        if recent_limit <= 0 or recent_limit > 100:
            raise UsageError("usage_invalid_recent_limit")
        async with self._transaction() as session:
            await lock_usage_tenant(session, tenant_id)
            at = now or self.clock()
            resources = await self._resource_usage(session, tenant_id)
            current = await self._current_entitlement(session, tenant_id, at, lock=True)
            if current is None or (
                self.require_active_entitlement and current.provider_request_limit is None
            ):
                configured = self.require_active_entitlement or await self._has_entitlement(
                    session, tenant_id
                )
                return UsageSummary(
                    tenant_id=tenant_id,
                    enabled=False,
                    entitlement_status="inactive" if configured else "legacy",
                    plan_code=None,
                    version=None,
                    period_start=None,
                    period_end=None,
                    provider_request_limit=None,
                    provider_requests_used=0,
                    provider_requests_reserved=0,
                    provider_requests_remaining=0 if configured else None,
                    cost_status="unknown",
                    recent_events=(),
                    resources=resources,
                )
            await self._release_expired(session, current, at)
            events = (
                await session.scalars(
                    select(UsageEvent)
                    .where(
                        UsageEvent.tenant_id == tenant_id,
                        UsageEvent.entitlement_id == current.id,
                    )
                    .order_by(desc(UsageEvent.occurred_at), desc(UsageEvent.id))
                    .limit(recent_limit)
                )
            ).all()
            known_cost = await session.scalar(
                select(UsageEvent.id)
                .where(
                    UsageEvent.tenant_id == tenant_id,
                    UsageEvent.entitlement_id == current.id,
                    UsageEvent.estimated_cost.is_not(None),
                )
                .limit(1)
            )
            remaining = (
                None
                if current.provider_request_limit is None
                else max(
                    current.provider_request_limit
                    - current.provider_requests_used
                    - current.provider_requests_reserved,
                    0,
                )
            )
            cost_status = "known" if known_cost is not None else "unknown"
            quotas = await session.scalars(
                select(ProductQuota)
                .where(
                    ProductQuota.tenant_id == tenant_id, ProductQuota.entitlement_id == current.id
                )
                .order_by(ProductQuota.metric)
            )
            counts = (
                await session.execute(
                    select(
                        func.count(ProviderDispatch.id),
                        func.count(ProviderDispatch.id).filter(
                            ProviderDispatch.state.in_(
                                ("dispatched", "timeout", "transport_error", "cancelled", "unknown")
                            )
                        ),
                        func.count(ProviderDispatch.id).filter(
                            ProviderDispatch.estimated_cost.is_(None)
                        ),
                        func.count(ProviderDispatch.total_tokens),
                        func.coalesce(func.sum(ProviderDispatch.total_tokens), 0),
                    ).where(
                        ProviderDispatch.tenant_id == tenant_id,
                        ProviderDispatch.started_at >= current.period_start,
                        ProviderDispatch.started_at < current.period_end,
                    )
                )
            ).one()
            return UsageSummary(
                tenant_id=tenant_id,
                enabled=True,
                entitlement_status="active",
                plan_code=current.plan_code,
                version=current.version,
                period_start=current.period_start,
                period_end=current.period_end,
                provider_request_limit=current.provider_request_limit,
                provider_requests_used=current.provider_requests_used,
                provider_requests_reserved=current.provider_requests_reserved,
                provider_requests_remaining=remaining,
                cost_status=cost_status,
                recent_events=tuple(self._event_view(event) for event in events),
                resources=resources,
                product_quotas=tuple(
                    ProductQuotaView(
                        ProductMetric(q.metric), q.unit_limit, q.units_used, q.units_reserved
                    )
                    for q in quotas
                ),
                model_calls=ProviderUsageSummary(*counts),
            )

    async def _resource_usage(self, session: AsyncSession, tenant_id: UUID) -> TenantResourceUsage:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None:
            raise UsageError("usage_tenant_unavailable")
        seats = await membership_seats(session, tenant_id)
        return TenantResourceUsage(
            storage_limit_bytes=tenant.quota_bytes,
            storage_used_bytes=tenant.used_storage_bytes,
            storage_reserved_bytes=tenant.reserved_storage_bytes,
            storage_remaining_bytes=max(
                0, tenant.quota_bytes - tenant.used_storage_bytes - tenant.reserved_storage_bytes
            ),
            seats_used=seats.active,
            seat_limit=seats.limit,
            seats_remaining=seats.remaining,
        )

    @asynccontextmanager
    async def _transaction(
        self, session: AsyncSession | None = None
    ) -> AsyncIterator[AsyncSession]:
        try:
            if session is not None:
                yield session
            else:
                async with self.session_factory.begin() as owned:
                    yield owned
        except DBAPIError as error:
            raise UsageError("usage_store_unavailable") from error

    async def _reserve(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        quantity: int,
        source: str,
    ) -> ReservationResult:
        await lock_usage_tenant(session, tenant_id)
        now = self.clock()
        existing = await self._reservation(session, tenant_id, operation_id, lock=True)
        if existing is not None:
            if existing.metric != "provider_request" or existing.quantity != quantity:
                raise UsageError("usage_idempotency_conflict")
            return self._reservation_result(existing, replay=True)
        entitlement = await self._current_entitlement(session, tenant_id, now, lock=True)
        if entitlement is None:
            if self.require_active_entitlement or await self._has_entitlement(session, tenant_id):
                raise UsageError("usage_entitlement_inactive")
            return ReservationResult(tenant_id, operation_id, quantity, False, False, "legacy")
        if self.require_active_entitlement and entitlement.provider_request_limit is None:
            raise UsageError("usage_entitlement_inactive")
        # A concurrent request with the same operation may have inserted its
        # reservation while we waited for the entitlement row lock.  Re-read
        # after acquiring that lock so the loser returns a replay instead of
        # surfacing a unique-constraint IntegrityError.
        existing = await self._reservation(session, tenant_id, operation_id, lock=True)
        if existing is not None:
            if existing.metric != "provider_request" or existing.quantity != quantity:
                raise UsageError("usage_idempotency_conflict")
            return self._reservation_result(existing, replay=True)
        await self._release_expired(session, entitlement, now)
        limit = entitlement.provider_request_limit
        if limit is not None and (
            entitlement.provider_requests_used + entitlement.provider_requests_reserved + quantity
            > limit
        ):
            raise UsageError("usage_limit_reached")
        reservation = UsageReservation(
            id=uuid4(),
            tenant_id=tenant_id,
            entitlement_id=entitlement.id,
            operation_id=operation_id,
            quantity=quantity,
            state="reserved",
            expires_at=now + self.reservation_ttl,
            reserved_at=now,
            created_at=now,
            updated_at=now,
        )
        entitlement.provider_requests_reserved += quantity
        session.add(reservation)
        await session.flush()
        return self._reservation_result(reservation, replay=False, entitlement=entitlement)

    async def _settle(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        provider: str | None,
        model: str | None,
        usage: dict[str, Any] | None,
        currency: str | None,
        estimated_cost: Decimal | int | float | str | None,
        pricing_version: str | None,
        source: str,
    ) -> ReservationResult:
        await lock_usage_tenant(session, tenant_id)
        reservation = await self._reservation(session, tenant_id, operation_id, lock=True)
        if reservation is None:
            if self.require_active_entitlement:
                raise UsageError("usage_reservation_not_found")
            return ReservationResult(tenant_id, operation_id, 1, False, False, "legacy")
        entitlement = await self._entitlement(session, reservation, lock=True)
        if reservation.state == "consumed":
            existing_event = cast(
                UsageEvent | None,
                await session.scalar(
                    select(UsageEvent).where(
                        UsageEvent.tenant_id == tenant_id,
                        UsageEvent.reservation_id == reservation.id,
                        UsageEvent.event_type == "consume",
                    )
                ),
            )
            if existing_event is not None and not self._settlement_matches(
                existing_event,
                provider=provider,
                model=model,
                usage=usage,
                currency=currency,
                estimated_cost=estimated_cost,
                pricing_version=pricing_version,
                source=source,
            ):
                raise UsageError("usage_idempotency_conflict")
            return self._reservation_result(reservation, replay=True, entitlement=entitlement)
        if reservation.state != "reserved":
            raise UsageError("usage_reservation_not_settleable")
        now = self.clock()
        if reservation.expires_at <= now:
            await self._release_one(session, entitlement, reservation, now, source="expiry")
            raise UsageError("usage_reservation_expired")
        data = self._usage_values(usage, estimated_cost)
        reservation.state, reservation.consumed_at = "consumed", now
        entitlement.provider_requests_reserved -= reservation.quantity
        entitlement.provider_requests_used += reservation.quantity
        session.add(
            UsageEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                entitlement_id=entitlement.id,
                reservation_id=reservation.id,
                operation_id=operation_id,
                event_type="consume",
                quantity=reservation.quantity,
                provider=provider,
                model=model,
                input_tokens=data["input_tokens"],
                output_tokens=data["output_tokens"],
                total_tokens=data["total_tokens"],
                currency=currency,
                estimated_cost=data["estimated_cost"],
                pricing_version=pricing_version,
                source=source,
                occurred_at=now,
            )
        )
        await session.flush()
        return self._reservation_result(reservation, replay=False, entitlement=entitlement)

    async def _release(
        self, session: AsyncSession, *, tenant_id: UUID, operation_id: UUID, source: str
    ) -> ReservationResult:
        # Deactivation must not strand reservations for cancelled background work.
        await lock_usage_tenant(session, tenant_id, allow_inactive=True)
        reservation = await self._reservation(session, tenant_id, operation_id, lock=True)
        if reservation is None:
            return ReservationResult(tenant_id, operation_id, 1, False, False, "legacy")
        entitlement = await self._entitlement(session, reservation, lock=True)
        if reservation.state == "released":
            return self._reservation_result(reservation, replay=True, entitlement=entitlement)
        if reservation.state == "consumed":
            raise UsageError("usage_reservation_not_releasable")
        await self._release_one(session, entitlement, reservation, self.clock(), source=source)
        await session.flush()
        return self._reservation_result(reservation, replay=False, entitlement=entitlement)

    async def _release_expired(
        self, session: AsyncSession, entitlement: TenantEntitlement, now: datetime
    ) -> None:
        rows = (
            await session.scalars(
                select(UsageReservation)
                .where(
                    UsageReservation.tenant_id == entitlement.tenant_id,
                    UsageReservation.entitlement_id == entitlement.id,
                    UsageReservation.state == "reserved",
                    UsageReservation.expires_at <= now,
                )
                .with_for_update()
            )
        ).all()
        for reservation in rows:
            await self._release_one(session, entitlement, reservation, now, source="expiry")

    async def _release_one(
        self,
        session: AsyncSession,
        entitlement: TenantEntitlement,
        reservation: UsageReservation,
        now: datetime,
        *,
        source: str,
    ) -> None:
        if reservation.state != "reserved":
            return
        reservation.state, reservation.released_at = "released", now
        entitlement.provider_requests_reserved -= reservation.quantity
        session.add(
            UsageEvent(
                id=uuid4(),
                tenant_id=reservation.tenant_id,
                entitlement_id=entitlement.id,
                reservation_id=reservation.id,
                operation_id=reservation.operation_id,
                event_type="release",
                quantity=reservation.quantity,
                source=source,
                occurred_at=now,
            )
        )

    async def _has_entitlement(self, session: AsyncSession, tenant_id: UUID) -> bool:
        return (
            await session.scalar(
                select(TenantEntitlement.id)
                .where(TenantEntitlement.tenant_id == tenant_id)
                .limit(1)
            )
            is not None
        )

    async def _current_entitlement(
        self, session: AsyncSession, tenant_id: UUID, now: datetime, *, lock: bool
    ) -> TenantEntitlement | None:
        statement = (
            select(TenantEntitlement)
            .where(
                TenantEntitlement.tenant_id == tenant_id,
                TenantEntitlement.period_start <= now,
                TenantEntitlement.period_end > now,
            )
            .order_by(desc(TenantEntitlement.version), desc(TenantEntitlement.period_start))
        )
        if lock:
            statement = statement.with_for_update()
        return cast(TenantEntitlement | None, await session.scalar(statement))

    async def _reservation(
        self, session: AsyncSession, tenant_id: UUID, operation_id: UUID, *, lock: bool
    ) -> UsageReservation | None:
        statement = select(UsageReservation).where(
            UsageReservation.tenant_id == tenant_id,
            UsageReservation.operation_id == operation_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(UsageReservation | None, await session.scalar(statement))

    async def _entitlement(
        self, session: AsyncSession, reservation: UsageReservation, *, lock: bool
    ) -> TenantEntitlement:
        statement = select(TenantEntitlement).where(
            TenantEntitlement.tenant_id == reservation.tenant_id,
            TenantEntitlement.id == reservation.entitlement_id,
        )
        if lock:
            statement = statement.with_for_update()
        entitlement = cast(TenantEntitlement | None, await session.scalar(statement))
        if entitlement is None:
            raise UsageError("usage_unavailable")
        return entitlement

    @staticmethod
    def _reservation_result(
        reservation: UsageReservation,
        *,
        replay: bool,
        entitlement: TenantEntitlement | None = None,
    ) -> ReservationResult:
        return ReservationResult(
            tenant_id=reservation.tenant_id,
            operation_id=reservation.operation_id,
            quantity=reservation.quantity,
            ledgered=True,
            replay=replay,
            status=reservation.state,
            reservation_id=reservation.id,
            entitlement_id=reservation.entitlement_id,
            provider_request_limit=(
                entitlement.provider_request_limit if entitlement is not None else None
            ),
            provider_requests_used=(
                entitlement.provider_requests_used if entitlement is not None else None
            ),
            provider_requests_reserved=(
                entitlement.provider_requests_reserved if entitlement is not None else None
            ),
        )

    @staticmethod
    def _usage_values(
        usage: dict[str, Any] | None, estimated_cost: Decimal | int | float | str | None
    ) -> dict[str, int | Decimal | None]:
        usage = usage or {}
        values: dict[str, int | Decimal | None] = {}
        for key in ("input_tokens", "prompt_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                values["input_tokens"] = value
                break
        else:
            values["input_tokens"] = None
        for key in ("output_tokens", "completion_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                values["output_tokens"] = value
                break
        else:
            values["output_tokens"] = None
        total = usage.get("total_tokens")
        values["total_tokens"] = total if type(total) is int and total >= 0 else None
        if estimated_cost is None:
            values["estimated_cost"] = None
        else:
            try:
                decimal_cost = Decimal(str(estimated_cost))
            except Exception as error:
                raise UsageError("usage_invalid_cost") from error
            if not decimal_cost.is_finite() or decimal_cost < 0:
                raise UsageError("usage_invalid_cost")
            values["estimated_cost"] = decimal_cost
        return values

    @staticmethod
    def _event_view(event: UsageEvent) -> UsageEventView:
        return UsageEventView(
            event_type=event.event_type,
            quantity=event.quantity,
            operation_id=event.operation_id,
            provider=event.provider,
            model=event.model,
            total_tokens=event.total_tokens,
            estimated_cost=event.estimated_cost,
            currency=event.currency,
            pricing_version=event.pricing_version,
            source=event.source,
            occurred_at=event.occurred_at,
        )

    @classmethod
    def _settlement_matches(
        cls,
        event: UsageEvent,
        *,
        provider: str | None,
        model: str | None,
        usage: dict[str, Any] | None,
        currency: str | None,
        estimated_cost: Decimal | int | float | str | None,
        pricing_version: str | None,
        source: str,
    ) -> bool:
        values = cls._usage_values(usage, estimated_cost)
        supplied = {
            "provider": provider,
            "model": model,
            "input_tokens": values["input_tokens"],
            "output_tokens": values["output_tokens"],
            "total_tokens": values["total_tokens"],
            "currency": currency,
            "estimated_cost": values["estimated_cost"],
            "pricing_version": pricing_version,
            "source": source,
        }
        # Omitted optional fields are allowed on a replay. Explicit values must
        # agree with the durable event, including an explicit null in usage.
        if provider is not None and event.provider != provider:
            return False
        if model is not None and event.model != model:
            return False
        if currency is not None and event.currency != currency:
            return False
        if pricing_version is not None and event.pricing_version != pricing_version:
            return False
        if source != "unknown" and event.source != source:
            return False
        for key in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost"):
            if usage is not None and key != "estimated_cost" and supplied[key] is not None:
                if getattr(event, key) != supplied[key]:
                    return False
        if estimated_cost is not None and event.estimated_cost != supplied["estimated_cost"]:
            return False
        return True
