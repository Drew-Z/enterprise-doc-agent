from __future__ import annotations

from collections.abc import Callable
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
    require_entitlement_operator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.locking import lock_usage_tenant
from enterprise_doc_core.billing.models import TenantEntitlement
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
                    if not self._matches(existing, configuration):
                        raise UsageError("entitlement_idempotency_conflict")
                    return EntitlementConfigurationResult(self._snapshot(existing), True)
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
                    },
                )
                return EntitlementConfigurationResult(self._snapshot(entitlement), False)
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
                return self._snapshot(entitlement)
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
                rows = await session.scalars(
                    select(TenantEntitlement)
                    .where(TenantEntitlement.tenant_id == tenant_id)
                    .order_by(desc(TenantEntitlement.version))
                    .limit(limit)
                )
                return tuple(self._snapshot(row) for row in rows)
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
    def _snapshot(row: TenantEntitlement) -> EntitlementSnapshot:
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
        )
