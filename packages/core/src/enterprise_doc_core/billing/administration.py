from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    EntitlementConfigurationResult,
    EntitlementSnapshot,
    PlatformEntitlementOperator,
    ProductQuotaConfiguration,
    require_entitlement_operator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.models import TenantEntitlement
from enterprise_doc_core.billing.product_contracts import ProductMetric, ProductQuotaView
from enterprise_doc_core.billing.product_models import ProductQuota
from enterprise_doc_core.identity.models import Tenant


class EntitlementAdministrationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    async def configure(
        self,
        *,
        tenant_id: UUID,
        operator: PlatformEntitlementOperator,
        configuration: EntitlementConfiguration,
    ) -> EntitlementConfigurationResult:
        operator = require_entitlement_operator(operator)
        try:
            async with self.session_factory.begin() as session:
                await lock_usage_tenant(session, tenant_id)
                existing = await self._find(session, tenant_id, configuration.entitlement_id)
                if existing is not None:
                    quotas = await self._quotas(session, tenant_id, [existing.id])
                    if not self._matches(existing, configuration) or not self._quotas_match(
                        quotas.get(existing.id, ()), configuration
                    ):
                        raise UsageError("entitlement_idempotency_conflict")
                    return EntitlementConfigurationResult(
                        self._snapshot(existing, quotas.get(existing.id, ())), True
                    )
                now = self.clock()
                if configuration.period_end <= now:
                    raise UsageError("entitlement_period_ended")
                latest_version = (
                    cast(
                        int | None,
                        await session.scalar(
                            select(func.max(TenantEntitlement.version)).where(
                                TenantEntitlement.tenant_id == tenant_id
                            )
                        ),
                    )
                    or 0
                )
                if latest_version != configuration.expected_version:
                    raise UsageError("entitlement_version_conflict")
                overlap = await session.scalar(
                    select(TenantEntitlement.id)
                    .where(
                        TenantEntitlement.tenant_id == tenant_id,
                        TenantEntitlement.period_start < configuration.period_end,
                        TenantEntitlement.period_end > configuration.period_start,
                    )
                    .limit(1)
                )
                if overlap is not None:
                    raise UsageError("entitlement_period_overlap")
                entitlement = TenantEntitlement(
                    id=configuration.entitlement_id,
                    tenant_id=tenant_id,
                    version=configuration.expected_version + 1,
                    plan_code=configuration.plan_code,
                    period_start=configuration.period_start,
                    period_end=configuration.period_end,
                    provider_request_limit=configuration.provider_request_limit,
                    provider_requests_used=0,
                    provider_requests_reserved=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(entitlement)
                await session.flush()
                product_quotas = (
                    ProductQuotaView(
                        ProductMetric.AGENT_TASK, configuration.agent_task_limit, 0, 0
                    ),
                    ProductQuotaView(
                        ProductMetric.DOCUMENT_BYTES, configuration.document_bytes_limit, 0, 0
                    ),
                )
                session.add_all(
                    ProductQuota(
                        tenant_id=tenant_id,
                        entitlement_id=entitlement.id,
                        metric=quota.metric.value,
                        unit_limit=quota.limit,
                        units_used=0,
                        units_reserved=0,
                    )
                    for quota in product_quotas
                )
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=None,
                    action="billing.entitlement.configured",
                    resource_type="tenant_entitlement",
                    resource_id=entitlement.id,
                    request_id=str(configuration.entitlement_id),
                    occurred_at=now,
                    metadata={
                        "operator_id": operator.operator_id,
                        "reason": operator.reason,
                        "plan_code": entitlement.plan_code,
                        "version": entitlement.version,
                        "period_start": entitlement.period_start.isoformat(),
                        "period_end": entitlement.period_end.isoformat(),
                        "provider_request_limit": entitlement.provider_request_limit,
                        "agent_task_limit": configuration.agent_task_limit,
                        "document_bytes_limit": configuration.document_bytes_limit,
                    },
                )
                return EntitlementConfigurationResult(
                    self._snapshot(entitlement, product_quotas), False
                )
        except IntegrityError as error:
            raise UsageError("entitlement_configuration_conflict") from error
        except DBAPIError as error:
            raise UsageError("entitlement_store_unavailable") from error

    async def configure_products(
        self,
        *,
        tenant_id: UUID,
        operator: PlatformEntitlementOperator,
        configuration: ProductQuotaConfiguration,
    ) -> EntitlementConfigurationResult:
        operator = require_entitlement_operator(operator)
        try:
            async with self.session_factory.begin() as session:
                await lock_usage_tenant(session, tenant_id)
                row = await self._find(session, tenant_id, configuration.entitlement_id)
                if row is None:
                    raise UsageError("entitlement_not_found")
                if row.version != configuration.expected_version:
                    raise UsageError("entitlement_version_conflict")
                existing = (await self._quotas(session, tenant_id, [row.id])).get(row.id, ())
                requested = {
                    ProductMetric.AGENT_TASK: configuration.agent_task_limit,
                    ProductMetric.DOCUMENT_BYTES: configuration.document_bytes_limit,
                }
                limits = {quota.metric: quota.limit for quota in existing}
                if any(limit != requested[metric] for metric, limit in limits.items()):
                    raise UsageError("entitlement_idempotency_conflict")
                if len(limits) == len(requested):
                    return EntitlementConfigurationResult(self._snapshot(row, existing), True)
                now = self.clock()
                if row.period_end <= now:
                    raise UsageError("entitlement_period_ended")
                session.add_all(
                    ProductQuota(
                        tenant_id=tenant_id,
                        entitlement_id=row.id,
                        metric=metric.value,
                        unit_limit=limit,
                        units_used=0,
                        units_reserved=0,
                    )
                    for metric, limit in requested.items()
                    if metric not in limits
                )
                await session.flush()
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=None,
                    action="billing.product_quotas.configured",
                    resource_type="tenant_entitlement",
                    resource_id=row.id,
                    request_id=str(row.id),
                    occurred_at=now,
                    metadata={
                        "operator_id": operator.operator_id,
                        "reason": operator.reason,
                        "version": row.version,
                        "agent_task_limit": configuration.agent_task_limit,
                        "document_bytes_limit": configuration.document_bytes_limit,
                    },
                )
                quotas = (await self._quotas(session, tenant_id, [row.id]))[row.id]
                return EntitlementConfigurationResult(self._snapshot(row, quotas), False)
        except IntegrityError as error:
            raise UsageError("entitlement_configuration_conflict") from error
        except DBAPIError as error:
            raise UsageError("entitlement_store_unavailable") from error

    async def show(
        self,
        *,
        tenant_id: UUID,
        entitlement_id: UUID,
        operator: PlatformEntitlementOperator,
    ) -> EntitlementSnapshot:
        require_entitlement_operator(operator)
        try:
            async with self.session_factory() as session:
                entitlement = await self._find(session, tenant_id, entitlement_id)
                if entitlement is None:
                    raise UsageError("entitlement_not_found")
                quotas = await self._quotas(session, tenant_id, [entitlement.id])
                return self._snapshot(entitlement, quotas.get(entitlement.id, ()))
        except DBAPIError as error:
            raise UsageError("entitlement_store_unavailable") from error

    async def list(
        self,
        *,
        tenant_id: UUID,
        operator: PlatformEntitlementOperator,
        limit: int = 20,
    ) -> tuple[EntitlementSnapshot, ...]:
        require_entitlement_operator(operator)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise UsageError("entitlement_invalid_limit")
        try:
            async with self.session_factory() as session:
                if await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None:
                    raise UsageError("entitlement_not_found")
                rows = (
                    await session.scalars(
                        select(TenantEntitlement)
                        .where(TenantEntitlement.tenant_id == tenant_id)
                        .order_by(desc(TenantEntitlement.version))
                        .limit(limit)
                    )
                ).all()
                quotas = await self._quotas(session, tenant_id, [row.id for row in rows])
                return tuple(self._snapshot(row, quotas.get(row.id, ())) for row in rows)
        except DBAPIError as error:
            raise UsageError("entitlement_store_unavailable") from error

    @staticmethod
    async def _find(
        session: AsyncSession, tenant_id: UUID, entitlement_id: UUID
    ) -> TenantEntitlement | None:
        return cast(
            TenantEntitlement | None,
            await session.scalar(
                select(TenantEntitlement).where(
                    TenantEntitlement.tenant_id == tenant_id, TenantEntitlement.id == entitlement_id
                )
            ),
        )

    @staticmethod
    def _matches(row: TenantEntitlement, configuration: EntitlementConfiguration) -> bool:
        return (
            row.version == configuration.expected_version + 1
            and row.plan_code == configuration.plan_code
            and row.period_start == configuration.period_start
            and row.period_end == configuration.period_end
            and row.provider_request_limit == configuration.provider_request_limit
        )

    @staticmethod
    def _snapshot(
        row: TenantEntitlement, quotas: tuple[ProductQuotaView, ...] = ()
    ) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            entitlement_id=row.id,
            tenant_id=row.tenant_id,
            plan_code=row.plan_code,
            version=row.version,
            period_start=row.period_start,
            period_end=row.period_end,
            provider_request_limit=row.provider_request_limit,
            provider_requests_used=row.provider_requests_used,
            provider_requests_reserved=row.provider_requests_reserved,
            created_at=row.created_at,
            product_quotas=quotas,
        )

    @staticmethod
    async def _quotas(
        session: AsyncSession, tenant_id: UUID, entitlement_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[ProductQuotaView, ...]]:
        rows = await session.scalars(
            select(ProductQuota)
            .where(
                ProductQuota.tenant_id == tenant_id,
                ProductQuota.entitlement_id.in_(entitlement_ids),
            )
            .order_by(ProductQuota.metric)
        )
        result: dict[UUID, tuple[ProductQuotaView, ...]] = {}
        for row in rows:
            result[row.entitlement_id] = (
                *result.get(row.entitlement_id, ()),
                ProductQuotaView(
                    ProductMetric(row.metric), row.unit_limit, row.units_used, row.units_reserved
                ),
            )
        return result

    @staticmethod
    def _quotas_match(
        quotas: tuple[ProductQuotaView, ...], configuration: EntitlementConfiguration
    ) -> bool:
        limits = {quota.metric: quota.limit for quota in quotas}
        # Old periods without a product quota never silently gain one on replay.
        return (
            limits.get(ProductMetric.AGENT_TASK, 0) == configuration.agent_task_limit
            and limits.get(ProductMetric.DOCUMENT_BYTES, 0) == configuration.document_bytes_limit
        )
