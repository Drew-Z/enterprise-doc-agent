from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.product_contracts import ProductMetric, ProductReservationResult
from enterprise_doc_core.billing.product_models import (
    ProductQuota,
    ProductUsageEvent,
    ProductUsageReservation,
)
from enterprise_doc_core.config import AppEnvironment


class ProductUsageService:
    """Durable business reservations; terminal business state owns their lifetime.

    Admission locks Tenant first. All counter changes lock quota before reservation.
    Terminal settlement must not acquire Tenant after a caller's Job/AgentRun lock.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        app_env: AppEnvironment = AppEnvironment.LOCAL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.require_active_entitlement = app_env in {
            AppEnvironment.STAGING,
            AppEnvironment.PRODUCTION,
        }

    @asynccontextmanager
    async def _transaction(self, session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
        try:
            if session is not None:
                yield session
            else:
                async with self.session_factory.begin() as owned:
                    yield owned
        except DBAPIError as error:
            raise UsageError("usage_store_unavailable") from error

    async def reserve(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
        quantity: int = 1,
        session: AsyncSession | None = None,
    ) -> ProductReservationResult:
        if not isinstance(metric, ProductMetric):
            raise UsageError("usage_invalid_metric")
        if (
            type(quantity) is not int
            or not 0 < quantity <= 2**63 - 1
            or (metric is ProductMetric.AGENT_TASK and quantity != 1)
        ):
            raise UsageError("usage_invalid_quantity")
        async with self._transaction(session) as transaction:
            await lock_usage_tenant(transaction, tenant_id)
            existing = await self._reservation(transaction, tenant_id, operation_id, metric)
            if existing is not None:
                existing_quota, existing = await self._locked_reservation(transaction, existing)
                if existing.quantity != quantity:
                    raise UsageError("usage_idempotency_conflict")
                return self._result(existing, existing_quota, replay=True)
            now = self.clock()
            period = await transaction.scalar(
                select(TenantEntitlement).where(
                    TenantEntitlement.tenant_id == tenant_id,
                    TenantEntitlement.period_start <= now,
                    TenantEntitlement.period_end > now,
                )
            )
            if period is None:
                if (
                    self.require_active_entitlement
                    or await transaction.scalar(
                        select(TenantEntitlement.id)
                        .where(TenantEntitlement.tenant_id == tenant_id)
                        .limit(1)
                    )
                    is not None
                ):
                    raise UsageError("usage_entitlement_inactive")
                return ProductReservationResult(
                    tenant_id, operation_id, metric, quantity, False, False, "legacy"
                )
            quota = await transaction.scalar(
                select(ProductQuota)
                .where(
                    ProductQuota.tenant_id == tenant_id,
                    ProductQuota.entitlement_id == period.id,
                    ProductQuota.metric == metric.value,
                )
                .with_for_update()
            )
            if quota is None:
                raise UsageError("usage_product_quota_unconfigured")
            if quantity > quota.unit_limit - quota.units_used - quota.units_reserved:
                raise UsageError("usage_limit_reached")
            reservation = ProductUsageReservation(
                tenant_id=tenant_id,
                quota_id=quota.id,
                operation_id=operation_id,
                metric=metric.value,
                quantity=quantity,
                state="reserved",
                reserved_at=now,
            )
            quota.units_reserved += quantity
            transaction.add(reservation)
            await transaction.flush()
            return self._result(reservation, quota, replay=False)

    async def require_reserved(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
        session: AsyncSession,
    ) -> None:
        reservation = await self._reservation(session, tenant_id, operation_id, metric)
        if reservation is None:
            if (
                self.require_active_entitlement
                or await session.scalar(
                    select(TenantEntitlement.id)
                    .where(TenantEntitlement.tenant_id == tenant_id)
                    .limit(1)
                )
                is not None
            ):
                raise UsageError("usage_reservation_not_found")
            return
        _, reservation = await self._locked_reservation(session, reservation)
        if reservation.state != "reserved":
            raise UsageError("usage_reservation_not_executable")

    async def settle(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
        source: str = "unknown",
        session: AsyncSession | None = None,
    ) -> ProductReservationResult:
        async with self._transaction(session) as transaction:
            return await self.finish_in_session(
                transaction,
                tenant_id=tenant_id,
                operation_id=operation_id,
                metric=metric,
                source=source,
                consume=True,
                require_reservation=self.require_active_entitlement,
                clock=self.clock,
            )

    async def release(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
        source: str = "unknown",
        session: AsyncSession | None = None,
    ) -> ProductReservationResult:
        async with self._transaction(session) as transaction:
            return await self.finish_in_session(
                transaction,
                tenant_id=tenant_id,
                operation_id=operation_id,
                metric=metric,
                source=source,
                consume=False,
                require_reservation=self.require_active_entitlement,
                clock=self.clock,
            )

    @staticmethod
    async def finish_in_session(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
        source: str,
        consume: bool,
        require_reservation: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> ProductReservationResult:
        if not isinstance(metric, ProductMetric):
            raise UsageError("usage_invalid_metric")
        if (
            not source
            or len(source) > 80
            or any(ord(char) < 32 or ord(char) == 127 for char in source)
        ):
            raise UsageError("usage_invalid_source")
        reservation = await ProductUsageService._reservation(
            session, tenant_id, operation_id, metric
        )
        if reservation is None:
            if consume and (
                require_reservation
                or await session.scalar(
                    select(TenantEntitlement.id)
                    .where(TenantEntitlement.tenant_id == tenant_id)
                    .limit(1)
                )
                is not None
            ):
                raise UsageError("usage_reservation_not_found")
            return ProductReservationResult(
                tenant_id, operation_id, metric, 0, False, False, "unreserved"
            )
        quota, reservation = await ProductUsageService._locked_reservation(session, reservation)
        target = "consumed" if consume else "released"
        if reservation.state == target:
            return ProductUsageService._result(reservation, quota, replay=True)
        if reservation.state != "reserved":
            raise UsageError(
                "usage_reservation_not_settleable"
                if consume
                else "usage_reservation_not_releasable"
            )
        now = clock() if clock is not None else datetime.now(UTC)
        reservation.state = target
        reservation.finished_at = now
        quota.units_reserved -= reservation.quantity
        if consume:
            quota.units_used += reservation.quantity
        session.add(
            ProductUsageEvent(
                tenant_id=tenant_id,
                reservation_id=reservation.id,
                event_type="consume" if consume else "release",
                source=source,
                occurred_at=now,
            )
        )
        await session.flush()
        return ProductUsageService._result(reservation, quota, replay=False)

    @staticmethod
    async def _reservation(
        session: AsyncSession,
        tenant_id: UUID,
        operation_id: UUID,
        metric: ProductMetric,
    ) -> ProductUsageReservation | None:
        return cast(
            ProductUsageReservation | None,
            await session.scalar(
                select(ProductUsageReservation).where(
                    ProductUsageReservation.tenant_id == tenant_id,
                    ProductUsageReservation.operation_id == operation_id,
                    ProductUsageReservation.metric == metric.value,
                )
            ),
        )

    @staticmethod
    async def _locked_reservation(
        session: AsyncSession,
        reservation: ProductUsageReservation,
    ) -> tuple[ProductQuota, ProductUsageReservation]:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        quota = await session.scalar(
            select(ProductQuota)
            .where(
                ProductQuota.tenant_id == reservation.tenant_id,
                ProductQuota.id == reservation.quota_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        locked = await session.scalar(
            select(ProductUsageReservation)
            .where(
                ProductUsageReservation.tenant_id == reservation.tenant_id,
                ProductUsageReservation.id == reservation.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if quota is None or locked is None:
            raise UsageError("usage_reservation_not_found")
        return quota, locked

    @staticmethod
    def _result(
        reservation: ProductUsageReservation,
        quota: ProductQuota,
        *,
        replay: bool,
    ) -> ProductReservationResult:
        return ProductReservationResult(
            tenant_id=reservation.tenant_id,
            operation_id=reservation.operation_id,
            metric=ProductMetric(reservation.metric),
            quantity=reservation.quantity,
            ledgered=True,
            replay=replay,
            status=reservation.state,
            reservation_id=reservation.id,
            entitlement_id=quota.entitlement_id,
        )
