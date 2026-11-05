from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.migration_database import migration_database

from enterprise_doc_core.billing.models import TenantEntitlement, UsageReservation
from enterprise_doc_core.identity.models import Tenant

pytestmark = pytest.mark.integration


async def test_migrated_usage_tables_supply_timestamps_without_rewriting_history():
    async with migration_database("20261012_0043") as database:
        tenant_id, entitlement_id, reservation_id = uuid4(), uuid4(), uuid4()
        original = datetime(2026, 1, 1, tzinfo=UTC)
        async with database.sessions.begin() as session:
            session.add(
                Tenant(id=tenant_id, name="migration", slug=tenant_id.hex, quota_bytes=1024)
            )
            await session.flush()
            session.add(
                TenantEntitlement(
                    id=entitlement_id,
                    tenant_id=tenant_id,
                    plan_code="history",
                    version=1,
                    period_start=original,
                    period_end=original + timedelta(days=1),
                    provider_request_limit=10,
                    provider_requests_used=3,
                    provider_requests_reserved=1,
                    created_at=original,
                    updated_at=original,
                )
            )
            await session.flush()
            session.add(
                UsageReservation(
                    id=reservation_id,
                    tenant_id=tenant_id,
                    entitlement_id=entitlement_id,
                    operation_id=uuid4(),
                    quantity=1,
                    reserved_at=original,
                    expires_at=original + timedelta(hours=1),
                    created_at=original,
                    updated_at=original,
                )
            )
        await database.migrate("head")
        async with database.sessions.begin() as session:
            entitlement = TenantEntitlement(
                tenant_id=tenant_id,
                plan_code="default",
                version=2,
                period_start=original + timedelta(days=1),
                period_end=original + timedelta(days=2),
                provider_request_limit=10,
            )
            session.add(entitlement)
            await session.flush()
            reservation = UsageReservation(
                tenant_id=tenant_id,
                entitlement_id=entitlement.id,
                operation_id=uuid4(),
                quantity=1,
                reserved_at=original,
                expires_at=original + timedelta(hours=1),
            )
            session.add(reservation)
            await session.flush()
            assert entitlement.created_at == entitlement.updated_at
            assert reservation.created_at == reservation.updated_at
            assert entitlement.created_at > original and reservation.created_at > original
        # Removing/reinstating defaults must neither delete nor rewrite historical rows.
        await database.migrate("20261012_0043")
        await database.migrate("head")
        async with database.sessions() as session:
            old = await session.get(TenantEntitlement, entitlement_id)
            held = await session.get(UsageReservation, reservation_id)
            assert old.created_at == old.updated_at == original
            assert (old.provider_requests_used, old.provider_requests_reserved) == (3, 1)
            assert held.created_at == held.updated_at == original
            assert held.state == "reserved"
            assert len((await session.scalars(select(TenantEntitlement))).all()) == 2
