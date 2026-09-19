from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.admission.models import TenantInitialEntitlement
from enterprise_doc_core.identity.models import Membership, Tenant


class MembershipSeatLimitReached(Exception):
    code = "membership_seat_limit_reached"


@dataclass(frozen=True, slots=True)
class MembershipSeats:
    active: int
    limit: int | None

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.active)


async def membership_seats(session: AsyncSession, tenant_id: UUID) -> MembershipSeats:
    limit = await session.scalar(
        select(TenantInitialEntitlement.seat_limit).where(
            TenantInitialEntitlement.tenant_id == tenant_id
        )
    )
    active = await session.scalar(
        select(func.count(Membership.id)).where(
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
    )
    return MembershipSeats(active=int(active or 0), limit=limit)


async def ensure_membership_capacity(session: AsyncSession, tenant_id: UUID) -> None:
    """Call before adding/reactivating Membership, in the writer's transaction."""
    tenant = await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise MembershipSeatLimitReached()
    seats = await membership_seats(session, tenant_id)
    # A missing initial entitlement retains legacy provisioning behavior.
    if seats.limit is not None and seats.active >= seats.limit:
        raise MembershipSeatLimitReached()
