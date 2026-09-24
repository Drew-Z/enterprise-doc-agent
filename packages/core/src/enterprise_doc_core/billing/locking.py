from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.identity.models import Tenant


async def lock_usage_tenant(
    session: AsyncSession, tenant_id: UUID, *, allow_inactive: bool = False
) -> None:
    """Serialize configuration and ledger work before locking their child rows."""
    try:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        statement = select(Tenant.id).where(Tenant.id == tenant_id)
        if not allow_inactive:
            statement = statement.where(Tenant.is_active.is_(True))
        found = await session.scalar(statement.with_for_update())
    except DBAPIError as error:
        raise UsageError("usage_store_unavailable") from error
    if found is None:
        raise UsageError("usage_tenant_unavailable")
